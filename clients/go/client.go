// Card Nim client in Go.  Standard library only, no modules needed:
//
//	go build -o client client.go
//	./client --server http://localhost:8000 --game K7PX --name "Go bot" [--seat 1]
//
// CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
// defaults, which is how the server starts this client for a seat.
//
// The state comes back as "key value" lines (?format=text), so there is no
// JSON to parse.  Put your strategy in chooseCard(); the rest is plumbing.
package main

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// ============================================================ your strategy

// chooseCard picks the card to play.
// Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
// Output:  one card from your hand.  A card larger than stones loses at once.
func chooseCard(stones int, mine, theirs []int) int {
	for _, c := range mine {
		if c == stones {
			return stones // take the win
		}
	}
	best, fallback := -1, -1
	for _, c := range mine {
		if c > stones {
			continue
		}
		if fallback < 0 || c < fallback {
			fallback = c
		}
		oppCanWin := false
		for _, o := range theirs {
			if o == stones-c { // do not leave an exact match
				oppCanWin = true
			}
		}
		if !oppCanWin && (best < 0 || c < best) {
			best = c
		}
	}
	if best >= 0 {
		return best
	}
	if fallback >= 0 {
		return fallback
	}
	return safestCard(stones, mine)
}

// safestCard is the smallest card that fits, played when the server refuses
// our choice, so a bug in the strategy cannot burn the clock.
func safestCard(stones int, mine []int) int {
	best := -1
	for _, c := range mine {
		if c <= stones && (best < 0 || c < best) {
			best = c
		}
	}
	if best >= 0 {
		return best
	}
	if len(mine) > 0 {
		return mine[0]
	}
	return 1
}

// ============================================================ plumbing

// getstate may hold the line for 60 s, so the client must wait longer than that.
var client = &http.Client{Timeout: 90 * time.Second}

// req makes one request.  Outputs: status and body; status 0 means the server
// could not be reached.
func req(method, u string) (int, string) {
	r, err := http.NewRequest(method, u, strings.NewReader(""))
	if err != nil {
		return 0, ""
	}
	res, err := client.Do(r)
	if err != nil {
		return 0, ""
	}
	defer res.Body.Close()
	body, _ := io.ReadAll(res.Body)
	return res.StatusCode, string(body)
}

// parseState turns "key value value" lines into a map of strings.
func parseState(text string) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSuffix(line, "\r")
		if line == "" {
			continue
		}
		if sp := strings.IndexByte(line, ' '); sp < 0 {
			out[line] = ""
		} else {
			out[line[:sp]] = line[sp+1:]
		}
	}
	return out
}

func nums(s string) []int {
	var out []int
	for _, f := range strings.Fields(s) {
		if n, err := strconv.Atoi(f); err == nil {
			out = append(out, n)
		}
	}
	return out
}

// opt reads the command line, then the environment, then a default.
func opt(flag, env, fallback string) string {
	for i := 1; i+1 < len(os.Args); i++ {
		if os.Args[i] == flag {
			return os.Args[i+1]
		}
	}
	if v := os.Getenv(env); v != "" {
		return v
	}
	return fallback
}

// ============================================================ game loop

func main() {
	server := strings.TrimRight(opt("--server", "CARDNIM_SERVER", "http://localhost:8000"), "/")
	game := opt("--game", "CARDNIM_GAME", "")
	name := opt("--name", "CARDNIM_NAME", "Go bot")
	seat := opt("--seat", "CARDNIM_SEAT", "")

	if game == "" {
		fmt.Fprintln(os.Stderr, "usage: client --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]")
		os.Exit(1)
	}
	base := server + "/api/games/" + game
	q := "?format=text&name=" + url.QueryEscape(name)
	if seat != "" {
		q += "&seat=" + seat
	}

	status, body := req("POST", base+"/join"+q)
	me := parseState(body)
	if status != 200 || me["token"] == "" {
		fmt.Fprintf(os.Stderr, "[%s] join failed (%d): %s\n", name, status, body)
		os.Exit(1)
	}
	token := me["token"]
	mySeat, _ := strconv.Atoi(me["seat"])
	fmt.Printf("[%s] joined %s as seat %d\n", name, game, mySeat)

	failures := 0
	for {
		// getstate answers only when it is our turn or the game is over, so
		// this loop does not poll and waiting costs nothing on the clock.
		status, body = req("GET", base+"/getstate?format=text&timeout=60&token="+token)
		if status == 0 {
			failures++
			if failures >= 30 {
				fmt.Fprintf(os.Stderr, "[%s] server gone\n", name)
				os.Exit(1)
			}
			time.Sleep(time.Second)
			continue
		}
		failures = 0
		if status != 200 {
			fmt.Fprintf(os.Stderr, "[%s] getstate failed (%d)\n", name, status)
			os.Exit(1)
		}

		s := parseState(body)
		if s["status"] == "finished" {
			w, _ := strconv.Atoi(s["winner"])
			verdict := "no winner"
			if w == mySeat {
				verdict = "WIN"
			} else if w != 0 {
				verdict = "LOSS"
			}
			fmt.Printf("[%s] game over: %s. %s\n", name, verdict, s["reason"])
			if w == mySeat {
				os.Exit(0)
			}
			os.Exit(2)
		}
		if s["your_turn"] != "1" {
			continue
		}

		stones, _ := strconv.Atoi(s["stones"])
		mine := nums(s["your_cards"])
		card := chooseCard(stones, mine, nums(s["opp_cards"]))
		fmt.Printf("[%s] %d stones, playing %d\n", name, stones, card)

		moveURL := fmt.Sprintf("%s/move?format=text&token=%s&card=%d", base, token, card)
		if status, _ = req("POST", moveURL); status != 200 {
			fmt.Fprintf(os.Stderr, "[%s] move %d rejected (%d)\n", name, card, status)
			if back := safestCard(stones, mine); back != card {
				req("POST", fmt.Sprintf("%s/move?format=text&token=%s&card=%d", base, token, back))
			} else {
				time.Sleep(time.Second) // not our turn any more: do not hammer it
			}
		}
	}
}
