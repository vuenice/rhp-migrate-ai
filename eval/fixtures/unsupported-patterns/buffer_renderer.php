<?php
class BufferRenderer {
    public function renderView(string $content): string {
        ob_start();
        echo "<div>" . $content . "</div>";
        $output = ob_get_clean();
        return (string)$output;
    }

    public function captureCallback(callable $callback): string {
        ob_start();
        $callback();
        return (string)ob_get_clean();
    }
}
