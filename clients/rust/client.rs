// Card Nim client in Rust.  No crates: the standard library has no HTTP
// client, so this speaks HTTP/1.1 over a TcpStream, the same way the C and
// C++ clients do.
//
//     rustc -O client.rs -o client
//     ./client --server http://localhost:8000 --game K7PX --name "Rust bot" [--seat 1]
//
// CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
// defaults, which is how the server starts this client for a seat.
//
// The state comes back as "key value" lines (?format=text), so there is no
// JSON to parse.  Put your strategy in choose_card(); the rest is plumbing.

use std::collections::HashMap;
use std::env;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::process::exit;
use std::thread::sleep;
use std::time::Duration;

// ============================================================ your strategy

/// Pick the card to play.
/// Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
/// Output:  one card from your hand.  A card larger than stones loses at once.
fn choose_card(stones: i64, mine: &[i64], theirs: &[i64]) -> i64 {
    if mine.contains(&stones) {
        return stones; // take the win
    }
    let mut fit: Vec<i64> = mine.iter().copied().filter(|&c| c <= stones).collect();
    fit.sort_unstable();
    if fit.is_empty() {
        return safest_card(stones, mine);
    }
    // do not leave the opponent an exact match
    match fit.iter().find(|&&c| !theirs.contains(&(stones - c))) {
        Some(&c) => c,
        None => fit[0],
    }
}

/// The smallest card that fits, played when the server refuses our choice, so
/// a bug in the strategy cannot burn the clock.
fn safest_card(stones: i64, mine: &[i64]) -> i64 {
    mine.iter()
        .copied()
        .filter(|&c| c <= stones)
        .min()
        .unwrap_or_else(|| mine.first().copied().unwrap_or(1))
}

// ============================================================ plumbing

/// One HTTP request.  Outputs: (status, body); status 0 means the server could
/// not be reached.
fn req(addr: &str, host: &str, method: &str, path: &str) -> (u16, String) {
    let mut stream = match TcpStream::connect(addr) {
        Ok(s) => s,
        Err(_) => return (0, String::new()),
    };
    // getstate may hold the line for 60 s, so wait longer than that
    let _ = stream.set_read_timeout(Some(Duration::from_secs(90)));
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return (0, String::new());
    }
    let mut raw = Vec::new();
    if stream.read_to_end(&mut raw).is_err() && raw.is_empty() {
        return (0, String::new());
    }
    let text = String::from_utf8_lossy(&raw).into_owned();
    let status = text
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .unwrap_or(0);
    let body = match text.find("\r\n\r\n") {
        Some(i) => text[i + 4..].to_string(),
        None => String::new(),
    };
    (status, body)
}

/// Turn "key value value" lines into a map of strings.
fn parse_state(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let line = line.trim_end_matches('\r');
        if line.is_empty() {
            continue;
        }
        match line.find(' ') {
            Some(sp) => out.insert(line[..sp].to_string(), line[sp + 1..].to_string()),
            None => out.insert(line.to_string(), String::new()),
        };
    }
    out
}

fn nums(s: &str) -> Vec<i64> {
    s.split_whitespace().filter_map(|f| f.parse().ok()).collect()
}

fn field(s: &HashMap<String, String>, key: &str) -> String {
    s.get(key).cloned().unwrap_or_default()
}

/// Percent-encode a name for a query parameter (team names have spaces).
fn encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The command line, then the environment, then a default.
fn opt(args: &[String], flag: &str, env_name: &str, fallback: &str) -> String {
    if let Some(i) = args.iter().position(|a| a == flag) {
        if i + 1 < args.len() {
            return args[i + 1].clone();
        }
    }
    match env::var(env_name) {
        Ok(v) if !v.is_empty() => v,
        _ => fallback.to_string(),
    }
}

// ============================================================ game loop

fn main() {
    let args: Vec<String> = env::args().collect();
    let server = opt(&args, "--server", "CARDNIM_SERVER", "http://localhost:8000");
    let game = opt(&args, "--game", "CARDNIM_GAME", "");
    let name = opt(&args, "--name", "CARDNIM_NAME", "Rust bot");
    let seat = opt(&args, "--seat", "CARDNIM_SEAT", "");

    if game.is_empty() {
        eprintln!("usage: client --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]");
        exit(1);
    }

    // "http://host:port" -> host, and the address TcpStream wants
    let hostport = server
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let host = hostport.split(':').next().unwrap_or("localhost").to_string();
    let addr = if hostport.contains(':') {
        hostport.clone()
    } else {
        format!("{hostport}:80")
    };

    let base = format!("/api/games/{game}");
    let query = format!(
        "?format=text&name={}{}",
        encode(&name),
        if seat.is_empty() { String::new() } else { format!("&seat={seat}") }
    );

    let (status, body) = req(&addr, &host, "POST", &format!("{base}/join{query}"));
    let me = parse_state(&body);
    let token = field(&me, "token");
    if status != 200 || token.is_empty() {
        eprintln!("[{name}] join failed ({status}): {body}");
        exit(1);
    }
    let my_seat: i64 = field(&me, "seat").parse().unwrap_or(0);
    println!("[{name}] joined {game} as seat {my_seat}");

    let mut failures = 0;
    loop {
        // getstate answers only when it is our turn or the game is over, so
        // this loop does not poll and waiting costs nothing on the clock.
        let path = format!("{base}/getstate?format=text&timeout=60&token={token}");
        let (status, body) = req(&addr, &host, "GET", &path);
        if status == 0 {
            failures += 1;
            if failures >= 30 {
                eprintln!("[{name}] server gone");
                exit(1);
            }
            sleep(Duration::from_secs(1));
            continue;
        }
        failures = 0;
        if status != 200 {
            eprintln!("[{name}] getstate failed ({status})");
            exit(1);
        }

        let s = parse_state(&body);
        if field(&s, "status") == "finished" {
            let w: i64 = field(&s, "winner").parse().unwrap_or(0);
            let verdict = if w == my_seat {
                "WIN"
            } else if w != 0 {
                "LOSS"
            } else {
                "no winner"
            };
            println!("[{name}] game over: {verdict}. {}", field(&s, "reason"));
            exit(if w == my_seat { 0 } else { 2 });
        }
        if field(&s, "your_turn") != "1" {
            continue;
        }

        let stones: i64 = field(&s, "stones").parse().unwrap_or(0);
        let mine = nums(&field(&s, "your_cards"));
        let card = choose_card(stones, &mine, &nums(&field(&s, "opp_cards")));
        println!("[{name}] {stones} stones, playing {card}");

        let move_path = format!("{base}/move?format=text&token={token}&card={card}");
        let (status, _) = req(&addr, &host, "POST", &move_path);
        if status != 200 {
            eprintln!("[{name}] move {card} rejected ({status})");
            let back = safest_card(stones, &mine);
            if back != card {
                let p = format!("{base}/move?format=text&token={token}&card={back}");
                req(&addr, &host, "POST", &p);
            } else {
                sleep(Duration::from_secs(1)); // not our turn any more
            }
        }
    }
}
