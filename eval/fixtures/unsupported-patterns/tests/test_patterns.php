<?php
require_once __DIR__ . '/../magic_container.php';
require_once __DIR__ . '/../buffer_renderer.php';

$container = new MagicContainer();
$container->framework = "RHP";
echo "Container item: " . $container->framework . "\n";
echo $container->customAction("arg1") . "\n";

$renderer = new BufferRenderer();
echo $renderer->renderView("Benchmark Content") . "\n";
