<?php
class ApiClient {
    private string $baseUrl;

    public function __construct(string $baseUrl) {
        $this->baseUrl = $baseUrl;
    }

    public function fetch(string $endpoint): string {
        $ch = curl_init($this->baseUrl . $endpoint);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 10);
        $response = curl_exec($ch);
        curl_close($ch);
        return (string)$response;
    }

    public function postData(string $endpoint, array $payload): string {
        $ch = curl_init($this->baseUrl . $endpoint);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
        $res = curl_exec($ch);
        curl_close($ch);
        return (string)$res;
    }
}
