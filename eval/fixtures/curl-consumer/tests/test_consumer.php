<?php
require_once __DIR__ . '/../request_handler.php';

$handler = new RequestHandler();

$coords = $handler->parseCoordinates([100, 200]);
echo "Coordinates: " . $coords['x'] . ", " . $coords['y'] . "\n";

$payload = $handler->formatUserPayload("dev_admin", "active");
echo "Payload: " . $payload['username'] . " is " . $payload['status'] . "\n";
