<?php
namespace App\Controllers;

use App\Models\Post;
use App\Models\User;

class PostController {
    /**
     * @var Post[]
     */
    private array $posts = [];

    public function index(): array {
        return $this->posts;
    }

    public function show(int $id): ?Post {
        foreach ($this->posts as $post) {
            if ($post->id === $id) {
                return $post;
            }
        }
        return null;
    }

    public function store(string $title, string $content, int $authorId): Post {
        $post = new Post(count($this->posts) + 1, $title, $content, $authorId);
        $this->posts[] = $post;
        return $post;
    }
}
