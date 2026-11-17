<?php
class ReflectionService {
    public function inspectClass(string $className): array {
        $reflector = new ReflectionClass($className);
        $methods = [];
        foreach ($reflector->getMethods() as $method) {
            $methods[] = $method->getName();
        }
        return $methods;
    }

    public function getParameterCount(string $className, string $methodName): int {
        $method = new ReflectionMethod($className, $methodName);
        return $method->getNumberOfParameters();
    }
}
