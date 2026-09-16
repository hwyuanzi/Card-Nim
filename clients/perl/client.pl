#!/usr/bin/env perl
# Card Nim client in Perl.  No CPAN: HTTP::Tiny has been core since 5.14.
#
#     perl client.pl --server http://localhost:8000 --game K7PX --name "Perl bot" [--seat 1]
#
# CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
# defaults, which is how the server starts this client for a seat.
#
# The state comes back as "key value" lines (?format=text), so there is no
# JSON to parse.  Put your strategy in choose_card(); the rest is plumbing.

use strict;
use warnings;
use HTTP::Tiny;

# ============================================================ your strategy

# Purpose: pick the card to play.
# Inputs:  stones on the table, your cards, the opponent's cards (array refs).
# Output:  one card from your hand.  A card larger than stones loses at once.
sub choose_card {
    my ($stones, $mine, $theirs) = @_;
    my %theirs = map { $_ => 1 } @$theirs;
    for my $c (@$mine) { return $stones if $c == $stones }   # take the win
    my @fit = sort { $a <=> $b } grep { $_ <= $stones } @$mine;
    return safest_card($stones, $mine) unless @fit;
    my @safe = grep { !$theirs{ $stones - $_ } } @fit;       # do not leave an exact match
    return @safe ? $safe[0] : $fit[0];
}

# Purpose: the smallest card that fits, played when the server refuses our
# choice, so a bug in the strategy cannot burn the clock.
sub safest_card {
    my ($stones, $mine) = @_;
    my @fit = sort { $a <=> $b } grep { $_ <= $stones } @$mine;
    return @fit ? $fit[0] : (@$mine ? $mine->[0] : 1);
}

# ============================================================ plumbing

my $http = HTTP::Tiny->new(timeout => 90);   # getstate may hold the line for 60 s

# Purpose: one request.  Outputs: (status, body); status 0 means unreachable.
sub req {
    my ($method, $url) = @_;
    my $res = $http->request($method, $url, { content => "" });
    return (0, "") unless $res;
    return ($res->{status} == 599 ? 0 : $res->{status}, $res->{content} // "");
}

# Purpose: turn "key value value" lines into a hash of strings.
sub parse_state {
    my ($text) = @_;
    my %out;
    for my $line (split /\n/, $text) {
        $line =~ s/\r$//;
        next unless length $line;
        my ($k, $v) = split / /, $line, 2;
        $out{$k} = defined $v ? $v : "";
    }
    return %out;
}

sub nums { return grep { /^-?\d+$/ } split /\s+/, ($_[0] // "") }

# Purpose: percent-encode a name for a query parameter (team names have
# spaces in them).
sub escape {
    my ($s) = @_;
    $s =~ s/([^A-Za-z0-9\-_.~])/sprintf("%%%02X", ord($1))/ge;
    return $s;
}

# Purpose: the command line over the environment over a default.
sub opt {
    my ($flag, $env, $default) = @_;
    for my $i (0 .. $#ARGV - 1) { return $ARGV[ $i + 1 ] if $ARGV[$i] eq $flag }
    return $ENV{$env} if defined $ENV{$env} && length $ENV{$env};
    return $default;
}

my $server = opt("--server", "CARDNIM_SERVER", "http://localhost:8000");
$server =~ s{/+$}{};
my $game = opt("--game", "CARDNIM_GAME", "");
my $name = opt("--name", "CARDNIM_NAME", "Perl bot");
my $seat = opt("--seat", "CARDNIM_SEAT", "");

unless (length $game) {
    print STDERR "usage: perl client.pl --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]\n";
    exit 1;
}

my $base = "$server/api/games/$game";

# ============================================================ join

my $query = "?format=text&name=" . escape($name) . (length $seat ? "&seat=$seat" : "");
my ($status, $body) = req("POST", "$base/join$query");
my %me = parse_state($body);
unless ($status == 200 && $me{token}) {
    print STDERR "[$name] join failed ($status): $body\n";
    exit 1;
}
my $token   = $me{token};
my $my_seat = $me{seat};
print "[$name] joined $game as seat $my_seat\n";

# ============================================================ game loop

my $failures = 0;
while (1) {
    # getstate answers only when it is our turn or the game is over, so this
    # loop does not poll and waiting costs nothing on the clock.
    ($status, $body) = req("GET", "$base/getstate?format=text&timeout=60&token=$token");
    if ($status == 0) {
        if (++$failures >= 30) { print STDERR "[$name] server gone\n"; exit 1 }
        sleep 1;
        next;
    }
    $failures = 0;
    if ($status != 200) { print STDERR "[$name] getstate failed ($status)\n"; exit 1 }

    my %s = parse_state($body);
    if (($s{status} // "") eq "finished") {
        my $w = $s{winner} // 0;
        my $verdict = $w == $my_seat ? "WIN" : $w ? "LOSS" : "no winner";
        print "[$name] game over: $verdict. " . ($s{reason} // "") . "\n";
        exit($w == $my_seat ? 0 : 2);
    }
    next unless ($s{your_turn} // "") eq "1";

    my $stones = $s{stones};
    my @mine   = nums($s{your_cards});
    my @theirs = nums($s{opp_cards});
    my $card   = choose_card($stones, \@mine, \@theirs);
    print "[$name] $stones stones, playing $card\n";

    ($status, $body) = req("POST", "$base/move?format=text&token=$token&card=$card");
    if ($status != 200) {
        my $back = safest_card($stones, \@mine);
        print STDERR "[$name] move $card rejected ($status)\n";
        if ($back != $card) {
            req("POST", "$base/move?format=text&token=$token&card=$back");
        } else {
            sleep 1;   # not our turn any more or a hiccup: do not hammer it
        }
    }
}
