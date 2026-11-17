<?php
require_once __DIR__ . '/../app/Models/User.php';
require_once __DIR__ . '/../app/Models/Post.php';
require_once __DIR__ . '/../app/Controllers/PostController.php';
require_once __DIR__ . '/../routes/web.php';

use App\Models\User;
use App\Models\Post;
use App\Controllers\PostController;
use App\Routing\Router;

$user = new User(1, "Alice", "alice@example.com");
echo $user->getDisplayName() . "\n";

$controller = new PostController();
$post = $controller->store("Welcome to RHP", "Modern strictly typed PHP replacement.", $user->id);
echo "Post ID: " . $post->id . "\n";
echo "Post Title: " . $post->title . "\n";
echo "Post Summary: " . $post->summary(15) . "\n";

$router = new Router();
$router->get('/posts', [PostController::class, 'index']);
echo $router->dispatch('GET', '/posts') . "\n";
