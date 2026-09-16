/ Card Nim client in q (kdb+).  No libraries: .Q.hg is all the HTTP we need.
//
/     q client.q -server http://localhost:8000 -game K7PX -name "q bot" [-seat 1]
//
/ CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
/ defaults, which is how the server starts this client for a seat.
//
/ Every call is a GET and the state comes back as "key value" lines
/ (?format=text), so there is no JSON to parse and no POST body to build:
//
/     status playing | waiting | finished
/     you 2                    your seat
/     your_turn 1              1 or 0
/     stones 37
/     your_cards 1 2 4 5
/     opp_cards 1 3 6
/     winner 0                 0, 1 or 2
/     reason ...
//
/ Put your strategy in choose_card below.  Everything else is plumbing.

/ ============================================================ your strategy

/ Purpose: the smallest card that fits.  Used when nothing fits at all and
/ when the server refuses our choice, so a bug in the strategy cannot burn
/ the clock.  Defined first: q resolves globals at call time, but declaring
/ before use keeps qlint quiet and reads better.
safest_card:{[stones;mine]
  fit:asc mine where mine<=stones;
  $[count fit; first fit; count mine; first mine; 1] }   / flat, not nested $[]

/ Purpose: pick the card to play.
/ Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
/ Output:  one card from your hand.  A card larger than stones loses at once.
choose_card:{[stones;mine;theirs]
  if[stones in mine; :stones];                       / take the win
  fit:asc mine where mine<=stones;                   / cards that still fit
  if[0=count fit; :safest_card[stones;mine]];        / nothing fits: any card loses
  safe:fit where not (stones-fit) in theirs;         / do not leave an exact match
  $[count safe; first safe; first fit] }

/ ============================================================ plumbing

hex:"0123456789ABCDEF"
safechars:.Q.a,.Q.A,.Q.n,"-_.~"

/ Purpose: percent-encode one string for a query parameter.
/ $[cond;then;else] takes an odd number of arguments, so the byte value is
/ computed inline rather than assigned to a temporary inside the branch.
enc:{raze {[c] $[c in safechars; enlist c; "%",hex ((`long$c) div 16;(`long$c) mod 16)]} each x}

/ Purpose: one GET; returns the body, or "" if the server refused or is gone.
hg:{[url] @[.Q.hg; `$":",url; {[e] -2 "http: ",e; ""}]}

/ Purpose: turn "key value value" lines into a dictionary of strings.
parsestate:{[t]
  lines:"\n" vs t;
  lines:{$[(0<count x) and "\r"=last x; -1_x; x]} each lines;
  lines:lines where 0<count each lines;
  if[0=count lines; :()!()];
  parts:" " vs/: lines;                       / ("status";"playing")
  ks:`$first each parts;                      / `status, `stones, ... (`keys` is a q builtin)
  vals:{" " sv 1_x} each parts;               / the rest of the line, spaces and all
  ks!vals }

/ Purpose: "1 2 3" -> 1 2 3, "" -> empty.  Anything unparseable is dropped
/ rather than left as a null that the strategy would have to think about.
nums:{
  if[0=count x; :`long$()];
  v:"J"$" " vs x;
  v where not null v }

/ Purpose: true when field k of state s holds exactly the text v.
/ parsestate yields char VECTORS, but "1" written in q source is a char ATOM,
/ and ~ is strict about that: "1"~enlist "1" is 0b.  Both sides are promoted
/ to lists so a one-character value such as your_turn compares correctly.
is:{[s;k;v] ((),v)~(),s k}

/ Purpose: check the pure functions without a server or a game.
/     q client.q -selftest
/ Loading this file at all proves it parses; the checks then prove the
/ strategy and the text parsing behave.  Run it after editing choose_card.
selftest:{[]
  bad:0;
  chk:{[label;got;want]
    ok:got~want;
    -1 ($[ok;"  ok    ";"  FAIL  "]),label,$[ok;"";"   got ",(-3!got)," want ",-3!want];
    not ok};
  / the text format
  s:parsestate "status playing\nyour_turn 1\nstones 37\nyour_cards 1 2 4\nreason a b c\n";
  bad+:chk["parsestate reads a key";s`status;"playing"];
  bad+:chk["parsestate reads a number";s`stones;"37"];
  bad+:chk["parsestate keeps inner spaces";s`reason;"a b c"];
  bad+:chk["is[] matches a one-character field";is[s;`your_turn;"1"];1b];
  bad+:chk["is[] matches a longer field";is[s;`status;"playing"];1b];
  bad+:chk["is[] rejects a different value";is[s;`status;"finished"];0b];
  / card lists
  bad+:chk["nums splits a card list";nums "1 2 4";1 2 4];
  bad+:chk["nums handles an empty hand";nums "";`long$()];
  / percent-encoding
  bad+:chk["enc escapes a space";enc "Team A";"Team%20A"];
  bad+:chk["enc leaves safe characters";enc "ab-1.2~3_4";"ab-1.2~3_4"];
  / the strategy itself
  bad+:chk["takes the win";choose_card[5;1 2 5;1 2 3];5];
  bad+:chk["Shasha's example: 5 stones, cards 1 2 3 -> play 1";choose_card[5;1 2 3;1 2 3];1];
  bad+:chk["falls back when nothing fits";safest_card[1;2 3];2];
  / the verdict
  -1 $[bad;(string bad)," CHECK(S) FAILED";"all checks passed"];
  exit $[bad;1;0]}

/ Purpose: "--server" -> `server (q's own flags keep their leading dash off).
strip:{$[(0<count x) and "-"=first x; .z.s 1_x; x]}

/ Purpose: command line (-server X, --server X) over the environment over a
/ default.  A flag is only given a value when the next token is not itself a
/ flag, so q's own valueless options (-q) cannot swallow the argument after
/ them.  Brackets are explicit throughout: q evaluates right to left, so
/ i+1<count a would parse as i+(1<count a).
args:{[]
  a:.z.x; d:()!(); i:0;
  while[i<count a;
    k:a[i];
    if[(0<count k) and "-"=first k;
      / `and` is min, not short-circuit: both sides are always evaluated, so
      / the bounds test has to be its own $[] or a[i+1] is read out of range.
      hasval:$[(i+1)<count a; not "-"=first a[i+1]; 0b];
      $[hasval; [d[`$strip k]:a[i+1]; i+:1]; d[`$strip k]:""]];
    i+:1];
  d}
opts:args[]
opt:{[k;e;d]
  if[k in key opts; if[count v:opts k; :v]];
  if[count v:getenv e; :v];
  d}

server:opt[`server;`CARDNIM_SERVER;"http://localhost:8000"]
game:  opt[`game;  `CARDNIM_GAME;  ""]
name:  opt[`name;  `CARDNIM_NAME;  "q bot"]
seat:  opt[`seat;  `CARDNIM_SEAT;  ""]
if[`selftest in key opts; selftest[]]

if[not count game; -2 "usage: q client.q -game ID [-server http://host:8000] [-name NAME] [-seat 1|2]"; exit 1]
while[(0<count server) and "/"=last server; server:-1_server]

base:server,"/api/games/",game

/ ============================================================ join

joined:parsestate hg[base,"/join?format=text&name=",enc[name],$[count seat;"&seat=",seat;""]]
if[not `token in key joined; -2 "[",name,"] join failed"; exit 1]
token:joined`token
myseat:"J"$joined`seat
-1 "[",name,"] joined ",game," as seat ",string myseat;

/ ============================================================ game loop

failures:0

/ Purpose: play one turn: read the state, choose, send, and fall back to the
/ smallest fitting card if the server refuses.
turn:{[s]
  stones:"J"$s`stones;
  mine:nums s`your_cards;
  theirs:nums s`opp_cards;
  card:choose_card[stones;mine;theirs];
  -1 "[",name,"] ",(string stones)," stones, playing ",string card;
  r:parsestate hg[base,"/move?format=text&token=",token,"&card=",string card];
  if[not `status in key r;
    back:safest_card[stones;mine];
    if[not back=card;
      -2 "[",name,"] ",(string card)," refused, playing ",string back;
      hg[base,"/move?format=text&token=",token,"&card=",string back]]];
  }

/ Purpose: report the result and leave with 0 for a win, 2 for a loss.
report:{[s]
  w:"J"$s`winner;
  -1 "[",name,"] game over: ",($[w=myseat;"WIN";w>0;"LOSS";"no winner"]),". ",s`reason;
  exit $[w=myseat;0;2] }

/ getstate answers only when it is our turn or the game is over, so this loop
/ does not poll and waiting costs us nothing on the clock.
while[1b;
  s:parsestate hg[base,"/getstate?format=text&timeout=60&token=",token];
  $[not `status in key s;
      [ failures+:1;
        if[failures>30; -2 "[",name,"] server gone, giving up"; exit 1];
        system "sleep 1" ];
    [ failures:0;
      $[is[s;`status;"finished"]; report s;
        is[s;`your_turn;"1"];       turn s;
        ::] ] ] ]
