<?php
// Card Nim client in PHP.  No extensions beyond the default build: the http
// stream wrapper is enough.
//
//     php client.php --server http://localhost:8000 --game K7PX --name "PHP bot" [--seat 1]
//
// CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
// defaults, which is how the server starts this client for a seat.
//
// The state comes back as "key value" lines (?format=text), so there is no
// JSON to parse.  Put your strategy in choose_card(); the rest is plumbing.

// ============================================================ your strategy

// Purpose: pick the card to play.
// Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
// Output:  one card from your hand.  A card larger than stones loses at once.
function choose_card(int $stones, array $mine, array $theirs): int {
    if (in_array($stones, $mine, true)) return $stones;          // take the win
    $fit = array_values(array_filter($mine, fn($c) => $c <= $stones));
    sort($fit);
    if (!$fit) return safest_card($stones, $mine);
    $safe = array_values(array_filter($fit, fn($c) => !in_array($stones - $c, $theirs, true)));
    return $safe ? $safe[0] : $fit[0];                            // do not leave an exact match
}

// Purpose: the smallest card that fits, played when the server refuses our
// choice, so a bug in the strategy cannot burn the clock.
function safest_card(int $stones, array $mine): int {
    $fit = array_values(array_filter($mine, fn($c) => $c <= $stones));
    sort($fit);
    return $fit ? $fit[0] : ($mine ? $mine[0] : 1);
}

// ============================================================ plumbing

// Purpose: one request.  Outputs: [status, body]; status 0 means unreachable.
function req(string $method, string $url): array {
    $ctx = stream_context_create(["http" => [
        "method" => $method,
        "header" => "Content-Length: 0\r\n",
        "timeout" => 90,            // getstate may hold the line for 60 s
        "ignore_errors" => true,    // read 4xx bodies instead of throwing
    ]]);
    $body = @file_get_contents($url, false, $ctx);
    if ($body === false) return [0, ""];
    $status = 0;
    foreach ($http_response_header ?? [] as $h) {
        if (preg_match('#^HTTP/\S+\s+(\d+)#', $h, $m)) $status = (int)$m[1];
    }
    return [$status, $body];
}

// Purpose: turn "key value value" lines into an array of strings.
function parse_state(string $text): array {
    $out = [];
    foreach (explode("\n", $text) as $line) {
        $line = rtrim($line, "\r");
        if ($line === "") continue;
        $sp = strpos($line, " ");
        if ($sp === false) $out[$line] = "";
        else $out[substr($line, 0, $sp)] = substr($line, $sp + 1);
    }
    return $out;
}

function nums(?string $s): array {
    if ($s === null || trim($s) === "") return [];
    return array_map('intval', preg_split('/\s+/', trim($s)));
}

// Purpose: the command line over the environment over a default.
function opt(string $flag, string $env, string $default): string {
    global $argv;
    for ($i = 1; $i + 1 < count($argv); $i++) {
        if ($argv[$i] === $flag) return $argv[$i + 1];
    }
    $v = getenv($env);
    return ($v !== false && $v !== "") ? $v : $default;
}

// ============================================================ game loop

$server = rtrim(opt("--server", "CARDNIM_SERVER", "http://localhost:8000"), "/");
$game   = opt("--game", "CARDNIM_GAME", "");
$name   = opt("--name", "CARDNIM_NAME", "PHP bot");
$seat   = opt("--seat", "CARDNIM_SEAT", "");

if ($game === "") {
    fwrite(STDERR, "usage: php client.php --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]\n");
    exit(1);
}

$base  = "$server/api/games/$game";
$query = "?format=text&name=" . rawurlencode($name) . ($seat !== "" ? "&seat=$seat" : "");

[$status, $body] = req("POST", $base . "/join" . $query);
$me = parse_state($body);
if ($status !== 200 || empty($me["token"])) {
    fwrite(STDERR, "[$name] join failed ($status): $body\n");
    exit(1);
}
$token   = $me["token"];
$my_seat = (int)$me["seat"];
echo "[$name] joined $game as seat $my_seat\n";

$failures = 0;
while (true) {
    // getstate answers only when it is our turn or the game is over, so this
    // loop does not poll and waiting costs nothing on the clock.
    [$status, $body] = req("GET", "$base/getstate?format=text&timeout=60&token=$token");
    if ($status === 0) {
        if (++$failures >= 30) { fwrite(STDERR, "[$name] server gone\n"); exit(1); }
        sleep(1);
        continue;
    }
    $failures = 0;
    if ($status !== 200) { fwrite(STDERR, "[$name] getstate failed ($status)\n"); exit(1); }

    $s = parse_state($body);
    if (($s["status"] ?? "") === "finished") {
        $w = (int)($s["winner"] ?? 0);
        $verdict = $w === $my_seat ? "WIN" : ($w ? "LOSS" : "no winner");
        echo "[$name] game over: $verdict. " . ($s["reason"] ?? "") . "\n";
        exit($w === $my_seat ? 0 : 2);
    }
    if (($s["your_turn"] ?? "") !== "1") continue;

    $stones = (int)$s["stones"];
    $mine   = nums($s["your_cards"] ?? "");
    $card   = choose_card($stones, $mine, nums($s["opp_cards"] ?? ""));
    echo "[$name] $stones stones, playing $card\n";

    [$status, ] = req("POST", "$base/move?format=text&token=$token&card=$card");
    if ($status !== 200) {
        fwrite(STDERR, "[$name] move $card rejected ($status)\n");
        $back = safest_card($stones, $mine);
        if ($back !== $card) req("POST", "$base/move?format=text&token=$token&card=$back");
        else sleep(1);   // not our turn any more or a hiccup: do not hammer it
    }
}
