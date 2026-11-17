<?php
namespace App\Routing;

use App\Controllers\PostController;

class Router {
    private array $routes = [];

    public function get(string $path, array $handler): void {
        $this->routes['GET'][$path] = $handler;
    }

    public function post(string $path, array $handler): void {
        $this->routes['POST'][$path] = $handler;
    }

    public function dispatch(string $method, string $path): string {
        if (isset($this->routes[$method][$path])) {
            $handler = $this->routes[$method][$path];
            return "Route: " . $handler[0] . "::" . $handler[1];
        }
        return "Route: 404";
    }
}

$router = new Router();
$router->get('/posts', [PostController::class, 'index']);
$router->get('/posts/show', [PostController::class, 'show']);
$router->post('/posts', [PostController::class, 'store']);
