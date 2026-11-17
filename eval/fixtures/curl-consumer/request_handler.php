<?php
class RequestHandler {
    public function handleRequest(): array {
        $userId = $_GET['user_id'] ?? 'default_user';
        $action = $_POST['action'] ?? 'read';

        $data = array();
        $data['user'] = $userId;
        $data['action'] = $action;
        $data['timestamp'] = 1700000000;
        return $data;
    }

    public function parseCoordinates(array $coords): array {
        list($x, $y) = $coords;
        $result = array();
        $result['x'] = $x;
        $result['y'] = $y;
        return $result;
    }

    public function formatUserPayload(string $username, string $status): array {
        $payload = array();
        $payload['username'] = $username;
        $payload['status'] = $status;
        $payload['active'] = true;
        return $payload;
    }
}
