<?php
namespace App\Models;

class Post {
    public int $id;
    public string $title;
    public string $content;
    public int $authorId;

    public function __construct(int $id, string $title, string $content, int $authorId) {
        $this->id = $id;
        $this->title = $title;
        $this->content = $content;
        $this->authorId = $authorId;
    }

    public function summary(int $limit = 50): string {
        return mb_substr($this->content, 0, $limit);
    }
}
