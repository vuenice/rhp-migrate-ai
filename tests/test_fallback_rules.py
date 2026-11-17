"""Unit tests for AST-based deterministic fallback rules and string splicing."""

from pathlib import Path
import pytest

from rhp_migrate.engine.fallback_rules import (
    FallbackTransformResult,
    TextEdit,
    apply_fallback_rules,
    apply_fallback_rules_to_file,
    apply_text_edits,
)


# ---------------------------------------------------------------------------
# String Splicing Engine Tests
# ---------------------------------------------------------------------------

def test_apply_text_edits_end_to_start():
    """Verify that splicing end-to-start preserves character/byte stability."""
    source = "The quick brown fox jumps over the lazy dog"
    # replace "lazy dog" (35:43) with "active cat"
    # replace "brown fox" (10:19) with "red wolf"
    edits = [
        TextEdit(start_byte=10, end_byte=19, replacement="red wolf"),
        TextEdit(start_byte=35, end_byte=43, replacement="active cat"),
    ]
    result = apply_text_edits(source, edits)
    assert result == "The quick red wolf jumps over the active cat"


def test_apply_text_edits_overlapping_safety():
    """Overlapping edits are filtered out, preventing corruption."""
    source = "abcdefghij"
    edits = [
        TextEdit(start_byte=2, end_byte=6, replacement="XYZ"),  # cdef -> XYZ
        TextEdit(start_byte=3, end_byte=5, replacement="12"),   # de -> 12 (overlaps)
    ]
    result = apply_text_edits(source, edits)
    # The outer/larger edit (2:6) is applied
    assert result == "abXYZghij"


def test_apply_text_edits_utf8():
    """Multibyte UTF-8 characters are handled accurately with byte offsets."""
    source = "Hello 🌍 World"  # '🌍' is 4 bytes
    # Find byte index of 'World'
    b_source = source.encode("utf-8")
    world_start = b_source.find(b"World")
    world_end = world_start + len(b"World")

    edits = [
        TextEdit(start_byte=world_start, end_byte=world_end, replacement="Universe"),
    ]
    result = apply_text_edits(source, edits)
    assert result == "Hello 🌍 Universe"


# ---------------------------------------------------------------------------
# Multibyte (mb_*) Function Transform Tests
# ---------------------------------------------------------------------------

def test_mb_strlen_transform():
    code = "<?php $len = mb_strlen($name);"
    res = apply_fallback_rules(code)
    assert res.modified is True
    assert "Str::length($name)" in res.transformed_code
    assert "RHP-FALLBACK-MB-STRLEN" in res.rules_applied


def test_mb_substr_transforms():
    # 2 args
    code2 = "<?php $sub = mb_substr($str, 0);"
    assert "Str::substr($str, 0)" in apply_fallback_rules(code2).transformed_code

    # 3 args
    code3 = "<?php $sub = mb_substr($str, 2, 5);"
    assert "Str::substr($str, 2, 5)" in apply_fallback_rules(code3).transformed_code

    # 4 args (UTF-8 encoding parameter dropped)
    code4 = "<?php $sub = mb_substr($str, 2, 5, 'UTF-8');"
    assert "Str::substr($str, 2, 5)" in apply_fallback_rules(code4).transformed_code


def test_mb_strpos_and_strrpos_transforms():
    code = """<?php
$p1 = mb_strpos($haystack, 'needle');
$p2 = mb_strpos($haystack, 'needle', 3, 'UTF-8');
$r1 = mb_strrpos($haystack, 'last');
"""
    res = apply_fallback_rules(code)
    assert "Str::find($haystack, 'needle')" in res.transformed_code
    assert "Str::find($haystack, 'needle', 3)" in res.transformed_code
    assert "Str::fromLast($haystack, 'last')" in res.transformed_code


def test_mb_casing_transforms():
    code = """<?php
$low = mb_strtolower($title);
$up = mb_strtoupper($title, 'UTF-8');
"""
    res = apply_fallback_rules(code)
    assert "Str::lowerCase($title)" in res.transformed_code
    assert "Str::upperCase($title)" in res.transformed_code


def test_mb_rune_and_other_transforms():
    code = """<?php
$ord = mb_ord('A');
$chr = mb_chr(65);
$w = mb_strwidth('A');
$parts = mb_str_split($text, 2);
$valid = mb_check_encoding($text);
$contains = mb_str_contains($h, $n);
$starts = mb_str_starts_with($h, $n);
$ends = mb_str_ends_with($h, $n);
"""
    res = apply_fallback_rules(code)
    assert "Rune::ofFirst('A')" in res.transformed_code
    assert "Rune::from(65)" in res.transformed_code
    assert "Rune::width('A')" in res.transformed_code
    assert "Str::split($text, 2)" in res.transformed_code
    assert "true" in res.transformed_code
    assert "Str::contains($h, $n)" in res.transformed_code
    assert "Str::startsWith($h, $n)" in res.transformed_code
    assert "Str::endsWith($h, $n)" in res.transformed_code


# ---------------------------------------------------------------------------
# Laravel Facade Transform Tests
# ---------------------------------------------------------------------------

def test_laravel_facade_scoped_calls():
    code = """<?php
\\Illuminate\\Support\\Facades\\DB::table('users')->get();
Illuminate\\Support\\Facades\\Log::info('testing');
\\Illuminate\\Support\\Str::contains($text, 'search');
\\Illuminate\\Support\\Arr::get($arr, 'key');
"""
    res = apply_fallback_rules(code)
    assert "DB::table('users')" in res.transformed_code
    assert "Log::info('testing')" in res.transformed_code
    assert "Str::contains($text, 'search')" in res.transformed_code
    assert "Arr::get($arr, 'key')" in res.transformed_code


def test_laravel_file_and_hash_facade_methods():
    code = """<?php
$data = File::get('/path/to/file');
File::put('/path/to/file', 'hello');
$hashed = Hash::make('password');
$valid = Hash::check('password', $hashed);
"""
    res = apply_fallback_rules(code)
    assert "File::read('/path/to/file')" in res.transformed_code
    assert "File::write('/path/to/file', 'hello')" in res.transformed_code
    assert "Crypt::hash('password')" in res.transformed_code
    assert "Crypt::verify('password', $hashed)" in res.transformed_code


def test_laravel_use_statements_stripped():
    code = """<?php
use Illuminate\\Support\\Facades\\Log;
use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\File;
use Illuminate\\Support\\Str;
use App\\Models\\User;

$user = User::find(1);
"""
    res = apply_fallback_rules(code)
    assert "use Illuminate\\Support\\Facades\\Log;" not in res.transformed_code
    assert "use Illuminate\\Support\\Facades\\DB;" not in res.transformed_code
    assert "use Illuminate\\Support\\Facades\\File;" not in res.transformed_code
    assert "use Illuminate\\Support\\Str;" not in res.transformed_code
    # Non-facade user model use statement preserved
    assert "use App\\Models\\User;" in res.transformed_code


# ---------------------------------------------------------------------------
# Laravel Global Helpers Tests
# ---------------------------------------------------------------------------

def test_laravel_helpers():
    code = """<?php
$t = now();
$d = today();
logger('application booted');
logger()->warning('disk space low');
info('user logged in');
$q = request('search');
$val = session('token');
cookie('pref', 'dark');
$resp = response()->json(['success' => true]);
"""
    res = apply_fallback_rules(code)
    assert "Time::now()" in res.transformed_code
    assert "Time::today()" in res.transformed_code
    assert "Log::info('application booted')" in res.transformed_code
    assert "Log::warning('disk space low')" in res.transformed_code
    assert "Log::info('user logged in')" in res.transformed_code
    assert "Request::get('search')" in res.transformed_code
    assert "Session::get('token')" in res.transformed_code
    assert "Cookie::set('pref', 'dark')" in res.transformed_code
    assert "Response::json(['success' => true])" in res.transformed_code


# ---------------------------------------------------------------------------
# File Operations Tests
# ---------------------------------------------------------------------------

def test_apply_fallback_rules_to_file(tmp_path: Path):
    php_file = tmp_path / "test.php"
    php_file.write_text("<?php $len = mb_strlen($str);", encoding="utf-8")

    # Read-only test
    res = apply_fallback_rules_to_file(php_file, in_place=False)
    assert res.modified is True
    assert "Str::length($str)" in res.transformed_code
    assert php_file.read_text(encoding="utf-8") == "<?php $len = mb_strlen($str);"

    # In-place test
    res2 = apply_fallback_rules_to_file(php_file, in_place=True)
    assert res2.modified is True
    assert php_file.read_text(encoding="utf-8") == "<?php $len = Str::length($str);"
