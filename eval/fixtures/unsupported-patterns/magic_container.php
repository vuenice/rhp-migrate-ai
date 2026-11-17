<?php
class MagicContainer {
    private array $storage = [];

    public function __get(string $name): mixed {
        return $this->storage[$name] ?? null;
    }

    public function __set(string $name, mixed $value): void {
        $this->storage[$name] = $value;
    }

    public function __call(string $name, array $arguments): mixed {
        return "Invoked dynamic method " . $name;
    }
}
