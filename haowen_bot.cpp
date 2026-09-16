// Haowen's Card Nim competition client (C++17, standard library + POSIX sockets).
// The strategy combines deadline-bounded exact search with selective iterative
// deepening.  Run `./haowen_bot --selftest` for the built-in solver checks.

#include <arpa/inet.h>
#include <netdb.h>
#include <fcntl.h>
#include <poll.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <cctype>
#include <cstdio>
#include <memory>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <map>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace strategy {

using Clock = std::chrono::steady_clock;
constexpr int INF = 30000;
constexpr int WIN = 20000;

struct Bits {
    std::array<uint64_t, 4> w{};
    void add(int c) { if (c >= 1 && c <= 256) w[(c - 1) >> 6] |= 1ULL << ((c - 1) & 63); }
    void del(int c) { if (c >= 1 && c <= 256) w[(c - 1) >> 6] &= ~(1ULL << ((c - 1) & 63)); }
    bool has(int c) const { return c >= 1 && c <= 256 && ((w[(c - 1) >> 6] >> ((c - 1) & 63)) & 1ULL); }
    bool empty() const { return !(w[0] | w[1] | w[2] | w[3]); }
    int count() const { return __builtin_popcountll(w[0]) + __builtin_popcountll(w[1]) +
                               __builtin_popcountll(w[2]) + __builtin_popcountll(w[3]); }
    int sum() const {
        int total = 0;
        for (int wi = 0; wi < 4; ++wi) {
            uint64_t x = w[wi];
            while (x) { int bit = __builtin_ctzll(x); x &= x - 1; total += wi * 64 + bit + 1; }
        }
        return total;
    }
    int first() const {
        for (int i = 0; i < 4; ++i) if (w[i]) return i * 64 + __builtin_ctzll(w[i]) + 1;
        return 999;
    }
};

static inline void trim(Bits& b, int stones) {
    int n = std::max(0, std::min(256, stones));
    int full = n >> 6, rem = n & 63;
    for (int i = full + (rem != 0); i < 4; ++i) b.w[i] = 0;
    if (full < 4 && rem) b.w[full] &= (1ULL << rem) - 1;
    if (full < 4 && !rem) for (int i = full; i < 4; ++i) b.w[i] = 0;
}

static inline uint64_t mix64(uint64_t x) {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

struct State { int stones; Bits me, opp; };

static inline std::pair<uint64_t,uint64_t> fingerprint(const State& p) {
    uint64_t a = mix64((uint64_t)p.stones ^ 0xa0761d6478bd642fULL);
    uint64_t b = mix64((uint64_t)p.stones ^ 0xe7037ed1a0b428dbULL);
    for (int i = 0; i < 4; ++i) {
        a ^= mix64(p.me.w[i] + 0x9e3779b97f4a7c15ULL * (i + 1));
        a ^= mix64(p.opp.w[i] + 0xd1b54a32d192ed03ULL * (i + 5));
        b ^= mix64(p.me.w[i] + 0x94d049bb133111ebULL * (i + 9));
        b ^= mix64(p.opp.w[i] + 0xbf58476d1ce4e5b9ULL * (i + 13));
    }
    return {a, b};
}

static std::vector<int> cards(const Bits& b) {
    std::vector<int> out;
    out.reserve(b.count());
    for (int wi = 0; wi < 4; ++wi) {
        uint64_t x = b.w[wi];
        while (x) {
            int bit = __builtin_ctzll(x); x &= x - 1;
            out.push_back(wi * 64 + bit + 1);
        }
    }
    return out;
}

static State child(const State& p, int c) {
    State q{p.stones - c, p.opp, p.me};
    q.opp.del(c);                 // the mover's hand becomes the child's opponent hand
    trim(q.me, q.stones);
    trim(q.opp, q.stones);
    return q;
}

struct ExactEntry {
    uint64_t k1 = 0, k2 = 0;
    uint16_t best = 0;
    int8_t result = 0;            // -1 loss, +1 win
};

struct SearchEntry {
    uint64_t k1 = 0, k2 = 0;
    int16_t value = 0;
    uint16_t best = 0;
    uint8_t depth = 0, bound = 0; // 1 exact, 2 lower, 3 upper
    uint16_t age = 0;
};

class Solver {
public:
    Solver() : exact_tt(1u << 21), search_tt(1u << 19) {}

    int choose(int stones, const std::vector<int>& mine, const std::vector<int>& theirs,
               double my_time) {
        State root{stones, {}, {}};
        for (int c : mine) if (c <= stones) root.me.add(c);
        for (int c : theirs) if (c <= stones) root.opp.add(c);
        auto legal = cards(root.me);
        if (legal.empty()) return mine.empty() ? 1 : mine.front();
        int fallback = legal.front();
        if (root.me.has(stones)) return stones;
        for (int c : legal) if (root.stones - c < root.opp.first()) return c;
        // If even every remaining card from both hands cannot empty the pile,
        // every card stays playable forever.  The game then reduces exactly to
        // who runs out of cards first; no tree search is necessary.
        if (root.me.sum() + root.opp.sum() <= stones) return legal.back();

        // Below 20 ms, even one unchecked search batch can exhaust the clock.
        // Keep immediate wins above, then avoid giving away an exact reply.
        if (my_time < 0.020) {
            for (int c : legal) if (!root.opp.has(stones - c)) return c;
            return fallback;
        }

        // Reserve most of the clock for future moves and networking.  Positions
        // become dramatically cheaper as the pile shrinks, so early moves are capped.
        double reserve = std::max(1.0, std::min(8.0, my_time * 0.10));
        double usable = std::max(0.015, my_time - reserve);
        double expected = std::max(3.0, std::min(30.0, stones / std::max(2.0, 0.55 * max_card(legal))));
        double budget_cap = my_time > 10.0 ? 2.0 : 1.25;
        double budget = std::min(budget_cap, usable / (expected + 2.0));
        if (stones <= 80 || legal.size() <= 12) budget = std::min(2.0, usable * 0.18);
        if (my_time < 3.0) budget = std::min(budget, std::max(0.004, my_time * 0.08));
        double scale = 1.0;
        if (const char* e = std::getenv("CARDNIM_THINK_SCALE"))
            scale = std::max(0.05, std::min(10.0, std::atof(e)));
        budget = std::min(std::max(0.008, budget * scale), std::max(0.008, my_time * 0.15));

        auto start = Clock::now();
        deadline = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(budget));
        stopped = false; nodes = 0; ++age;

        // Exact proof search gets the first portion of the budget.  Winning roots
        // often prove quickly; difficult losing roots fall through to bounded search.
        auto whole_deadline = deadline;
        double exact_share = legal.size() <= 18 ? 0.90 : (legal.size() >= 50 ? 0.86 : 0.58);
        deadline = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(budget * exact_share));
        int exact_best = 0;
        int er = exact(root, exact_best);
        if (er == 1 && exact_best) return exact_best;

        // In very long, card-sparse openings, a bounded search cannot see the
        // tactical endgame and used to make unstable low-card choices at its
        // horizon.  Exact search has already had a chance above.  If it did not
        // finish, spend the high cards in descending order while reserving the
        // single largest card; this keeps maximum future coverage and sharply
        // reduces the pile without exposing an exact reply.
        if (er == 0) {
            int top = std::max(max_present(root.me), max_present(root.opp));
            int ratio = top >= 28 ? 9 : 10;
            if (top > 0 && root.stones > ratio * top) {
                for (auto it = legal.rbegin(); it != legal.rend(); ++it) {
                    int c = *it;
                    if (c < top && !root.opp.has(root.stones - c)) return c;
                }
            }
        }

        deadline = whole_deadline; stopped = false;
        int best = tactical_choice(root, legal);
        int completed_best = best;
        // Iterative deepening stabilizes the move if a deeper iteration is interrupted.
        for (int depth = 2; depth <= 18 && !out_of_time(); ++depth) {
            int iteration_best = completed_best;
            bool complete = root_search(root, depth, iteration_best);
            if (!complete) break;
            completed_best = iteration_best;
            if (last_root_value >= WIN - 100) break;
        }
        return root.me.has(completed_best) ? completed_best : fallback;
    }

    // Unlimited exact solve, used only by --selftest.
    int solve_for_test(State p, int& best) {
        deadline = Clock::time_point::max(); stopped = false; nodes = 0;
        return exact(p, best);
    }

    int solve_for_seconds(State p, int& best, double seconds, uint64_t& visited) {
        deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(
                                      std::chrono::duration<double>(seconds));
        stopped = false; nodes = 0;
        int result = exact(p, best);
        visited = nodes;
        return result;
    }

private:
    std::vector<ExactEntry> exact_tt;
    std::vector<SearchEntry> search_tt;
    Clock::time_point deadline{};
    uint64_t nodes = 0;
    bool stopped = false;
    uint16_t age = 1;
    int last_root_value = 0;

    static int max_card(const std::vector<int>& v) { return v.empty() ? 1 : v.back(); }
    bool out_of_time() {
        if (stopped) return true;
        if ((++nodes & 1023ULL) == 0 && Clock::now() >= deadline) stopped = true;
        return stopped;
    }

    bool exact_probe(const State& p, int& result, int& best) {
        auto [a,b] = fingerprint(p);
        auto& e = exact_tt[a & (exact_tt.size() - 1)];
        if (e.result && e.k1 == a && e.k2 == b) { result = e.result; best = e.best; return true; }
        return false;
    }
    void exact_store(const State& p, int result, int best) {
        auto [a,b] = fingerprint(p);
        exact_tt[a & (exact_tt.size() - 1)] = {a,b,(uint16_t)best,(int8_t)result};
    }

    int move_priority(const State& p, int c) const {
        int r = p.stones - c;
        if (r == 0) return 100000000;
        if (r < p.opp.first()) return 90000000;
        if (p.opp.has(r)) return -90000000;
        State q = child(p, c);
        int opp_moves = q.me.count();
        int opp_safe = 0, opp_bad = 0;
        for (int d : cards(q.me)) {
            int rr = r - d;
            if (rr == 0) return -80000000;
            if (rr < q.opp.first()) ++opp_safe;
            else if (q.opp.has(rr)) ++opp_bad;
            else ++opp_safe;
        }
        // Low opponent freedom is the dominant ordering signal.  Retain a mix of
        // edge cards: exact winning moves frequently occur at 1 or the current max.
        int edge = std::max(c, p.me.first() + max_present(p.me) - c);
        return (opp_bad - opp_safe) * 600 - opp_moves * 20 + c * 3 + edge;
    }
    static int max_present(const Bits& b) {
        for (int i = 3; i >= 0; --i) if (b.w[i]) return i * 64 + 64 - __builtin_clzll(b.w[i]);
        return 0;
    }
    std::vector<int> ordered(const State& p, int tt_move = 0, int cap = 999) const {
        std::vector<std::pair<int,int>> scored;
        for (int c : cards(p.me))
            scored.push_back({c == tt_move ? std::numeric_limits<int>::max() : move_priority(p,c), c});
        std::stable_sort(scored.begin(), scored.end(), [](auto a, auto b){ return a.first > b.first; });
        std::vector<int> out;
        int n = std::min<int>(cap, scored.size()); out.reserve(n);
        for (int i = 0; i < n; ++i) out.push_back(scored[i].second);
        return out;
    }

    int exact(State p, int& best) {
        if (out_of_time()) return 0;
        trim(p.me, p.stones); trim(p.opp, p.stones);
        if (p.me.empty()) { best = 0; return -1; }
        if (p.me.has(p.stones)) { best = p.stones; return 1; }
        if (p.me.sum() + p.opp.sum() <= p.stones) {
            best = max_present(p.me);
            int r = p.me.count() > p.opp.count() ? 1 : -1;
            exact_store(p,r,best); return r;
        }
        int cached = 0;
        if (exact_probe(p, cached, best)) return cached;
        int min_opp = p.opp.first();
        for (int c : cards(p.me)) if (p.stones - c < min_opp) {
            best = c; exact_store(p, 1, c); return 1;
        }
        auto moves = ordered(p);
        int losing_choice = moves.empty() ? 0 : moves.front();
        for (int c : moves) {
            if (p.opp.has(p.stones - c)) continue; // hands opponent the last move
            State q = child(p,c); int reply = 0;
            int r = exact(q, reply);
            if (r == 0) return 0;
            if (r == -1) { best = c; exact_store(p,1,c); return 1; }
        }
        // Moves skipped above are known losses because the opponent holds the remainder.
        best = losing_choice;
        exact_store(p,-1,best);
        return -1;
    }

    int tactical_choice(const State& p, const std::vector<int>& legal) const {
        auto v = ordered(p);
        return v.empty() ? legal.front() : v.front();
    }

    int evaluate(const State& p) const {
        if (p.me.empty()) return -WIN;
        if (p.me.has(p.stones)) return WIN;
        int safe_me = 0, bad_me = 0, force_me = 0;
        for (int c : cards(p.me)) {
            int r = p.stones - c;
            if (r < p.opp.first()) ++force_me;
            else if (p.opp.has(r)) ++bad_me;
            else ++safe_me;
        }
        int safe_opp = 0, bad_opp = 0;
        for (int c : cards(p.opp)) {
            int r = p.stones - c;
            if (r < p.me.first() || !p.me.has(r)) ++safe_opp;
            else ++bad_opp;
        }
        if (force_me) return WIN - 20;
        if (!safe_me) return -9000 - bad_me;
        int mobility = p.me.count() - p.opp.count();
        int mins = p.opp.first() - p.me.first();
        int maxs = max_present(p.me) - max_present(p.opp);
        return 75 * (safe_me - safe_opp) + 18 * (bad_opp - bad_me) +
               9 * mobility + 3 * mins + maxs;
    }

    bool search_probe(const State& p, int depth, int alpha, int beta, int& value, int& best) {
        auto [a,b] = fingerprint(p); auto& e = search_tt[a & (search_tt.size()-1)];
        if (e.k1 != a || e.k2 != b) return false;
        best = e.best;
        if (e.depth < depth) return false;
        if (e.bound == 1 || (e.bound == 2 && e.value >= beta) || (e.bound == 3 && e.value <= alpha)) {
            value = e.value; return true;
        }
        return false;
    }
    void search_store(const State& p, int depth, int value, int best, int bound) {
        auto [a,b] = fingerprint(p); auto& e = search_tt[a & (search_tt.size()-1)];
        if (e.age != age || depth >= e.depth) e = {a,b,(int16_t)value,(uint16_t)best,(uint8_t)depth,(uint8_t)bound,age};
    }

    int negamax(State p, int depth, int alpha, int beta, int ply) {
        if (out_of_time()) return 0;
        trim(p.me,p.stones); trim(p.opp,p.stones);
        if (p.me.empty()) return -WIN + ply;
        if (p.me.has(p.stones)) return WIN - ply;
        if (p.me.sum() + p.opp.sum() <= p.stones)
            return p.me.count() > p.opp.count() ? WIN-ply : -WIN+ply;
        int exact_r=0, exact_b=0;
        if (exact_probe(p,exact_r,exact_b)) return exact_r > 0 ? WIN-ply : -WIN+ply;
        if (depth <= 0) return evaluate(p);
        int tt_best=0, tt_value=0;
        if (search_probe(p,depth,alpha,beta,tt_value,tt_best)) return tt_value;
        int old_alpha=alpha, best_move=0, best_val=-INF;
        int n = p.me.count();
        int cap = depth >= 8 ? 10 : depth >= 5 ? 14 : depth >= 3 ? 22 : n;
        auto moves = ordered(p,tt_best,cap);
        for (int c : moves) {
            int r=p.stones-c;
            int val;
            if (r < p.opp.first()) val = WIN-ply;
            else if (p.opp.has(r)) val = -WIN+ply;
            else val = -negamax(child(p,c),depth-1,-beta,-alpha,ply+1);
            if (stopped) return 0;
            if (val > best_val) { best_val=val; best_move=c; }
            alpha=std::max(alpha,val);
            if (alpha>=beta) break;
        }
        if (best_move==0) return -WIN+ply;
        int bound = best_val <= old_alpha ? 3 : (best_val >= beta ? 2 : 1);
        search_store(p,depth,best_val,best_move,bound);
        return best_val;
    }

    bool root_search(const State& root, int depth, int& best) {
        int alpha=-INF, best_val=-INF;
        auto moves=ordered(root,best);
        int candidate=best;
        for (int c:moves) {
            int r=root.stones-c;
            int val;
            if (r < root.opp.first()) val=WIN;
            else if (root.opp.has(r)) val=-WIN;
            else val=-negamax(child(root,c),depth-1,-INF,-alpha,1);
            if (stopped) return false;
            if (val>best_val) {best_val=val;candidate=c;}
            alpha=std::max(alpha,val);
        }
        best=candidate; last_root_value=best_val; return true;
    }
};

static Solver solver;

static int choose_card(int stones, const std::vector<int>& mine, const std::vector<int>& theirs,
                       double my_time) {
    return solver.choose(stones,mine,theirs,my_time);
}

// A deliberately simple, independent uint64 reference recurrence.
static bool reference_win(int s, uint64_t a, uint64_t b,
                          std::unordered_map<std::string,bool>& memo) {
    uint64_t lim = s >= 64 ? ~0ULL : ((1ULL << s) - 1);
    a &= lim; b &= lim;
    std::string key(reinterpret_cast<char*>(&s),sizeof(s));
    key.append(reinterpret_cast<char*>(&a),sizeof(a)); key.append(reinterpret_cast<char*>(&b),sizeof(b));
    auto it=memo.find(key); if(it!=memo.end()) return it->second;
    if(!a) return memo[key]=false;
    for(uint64_t x=a;x;x&=x-1){
        uint64_t bit=x&-x; int c=__builtin_ctzll(bit)+1;
        if(c==s || !reference_win(s-c,b,a^bit,memo)) return memo[key]=true;
    }
    return memo[key]=false;
}

static int selftest() {
    Solver test_solver;
    int checked=0;
    for(int k=1;k<=8;++k) for(int s=1;s<=std::min(45,k*(k+1)+3);++s){
        State p{s,{},{}}; uint64_t m=(1ULL<<k)-1;
        for(int c=1;c<=k;++c) p.me.add(c),p.opp.add(c);
        std::unordered_map<std::string,bool> memo;
        bool ref=reference_win(s,m,m,memo); int best=0;
        bool got=test_solver.solve_for_test(p,best)>0;
        if(ref!=got || (got && (best<1 || best>k || best>s))){
            std::cerr<<"selftest mismatch s="<<s<<" k="<<k<<" ref="<<ref<<" got="<<got<<" best="<<best<<"\n";
            return 1;
        }
        ++checked;
    }
    // Also exercise asymmetric midgames, not just the initial full hands.
    std::mt19937 rng(0xC4A2D11u);
    for(int k=1;k<=8;++k){
        uint64_t full=(1ULL<<k)-1;
        for(int trial=0;trial<400;++trial){
            int s=1+(int)(rng()%35); uint64_t a=rng()&full,b=rng()&full;
            std::unordered_map<std::string,bool> memo;
            bool ref=reference_win(s,a,b,memo);
            State p{s,{},{}};for(int c=1;c<=k;++c){if(a&(1ULL<<(c-1)))p.me.add(c);if(b&(1ULL<<(c-1)))p.opp.add(c);}
            int best=0;bool got=test_solver.solve_for_test(p,best)>0;
            if(ref!=got){
                std::cerr<<"midgame mismatch s="<<s<<" k="<<k<<" a="<<a<<" b="<<b
                         <<" ref="<<ref<<" got="<<got<<"\n";return 1;
            }
            ++checked;
        }
    }
    std::unordered_map<std::string,bool> known_memo;
    if(!reference_win(5,7,7,known_memo)) {
        // 5/1..3 is a second-player win: reference_win must be false.
    } else { std::cerr<<"known 5/3 example failed\n"; return 1; }
    std::cout<<"selftest ok: "<<checked<<" initial and asymmetric positions agree with independent exhaustive search\n";
    return 0;
}

} // namespace strategy

// ---------------------------------------------------------------- HTTP client
struct Url {
    std::string host;
    int port = 80;
    std::shared_ptr<addrinfo> addresses;
};
static Url parse_url(std::string s) {
    if (s.rfind("http://", 0) == 0) s = s.substr(7);
    while (!s.empty() && s.back() == '/') s.pop_back();
    Url u;
    size_t p = s.find(':');
    u.host = s.substr(0, p);
    if (p != std::string::npos) u.port = std::atoi(s.c_str() + p + 1);
    // Resolve once, before joining starts the game clock.
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(u.host.c_str(), std::to_string(u.port).c_str(), &hints, &res) == 0)
        u.addresses = std::shared_ptr<addrinfo>(res, freeaddrinfo);
    return u;
}
static std::string http(const Url& u, const std::string& method, const std::string& path,
                        const std::string& body, int& status, double seconds = 0.5) {
    status = 0;
    auto end = strategy::Clock::now() + std::chrono::duration_cast<strategy::Clock::duration>(
        std::chrono::duration<double>(seconds));
    auto ready = [&](int fd, short events) {
        for (;;) {
            double left = std::chrono::duration<double>(end - strategy::Clock::now()).count();
            if (left <= 0) return false;
            pollfd pfd{fd, events, 0};
            int r = poll(&pfd, 1, (int)std::ceil(left * 1000));
            if (r >= 0) return r > 0;
            if (errno != EINTR) return false;
        }
    };
    int fd = -1;
    for (auto* p = u.addresses.get(); p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        if (fcntl(fd, F_SETFL, O_NONBLOCK) == 0) {
            int r = connect(fd, p->ai_addr, p->ai_addrlen);
            if (r == 0) break;
            if (errno == EINPROGRESS && ready(fd, POLLOUT)) {
                int error = 0; socklen_t n = sizeof(error);
                if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &n) == 0 && error == 0) break;
            }
        }
        close(fd); fd = -1;
    }
    if (fd < 0) return "";
    std::ostringstream q;
    q << method << " " << path << " HTTP/1.1\r\nHost: " << u.host
      << "\r\nConnection: close\r\nContent-Type: application/x-www-form-urlencoded\r\n"
      << "Content-Length: " << body.size() << "\r\n\r\n" << body;
    std::string out = q.str();
    size_t sent = 0;
    while (sent < out.size()) {
        if (!ready(fd, POLLOUT)) { close(fd); return ""; }
        ssize_t n = send(fd, out.data() + sent, out.size() - sent, 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n <= 0) { close(fd); return ""; }
        sent += (size_t)n;
    }
    std::string resp;
    size_t cut = std::string::npos, length = 0;
    int response_status = 0;
    char buf[4096];
    while (resp.size() <= 1024 * 1024 && ready(fd, POLLIN)) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n <= 0) break;
        resp.append(buf, (size_t)n);
        if (cut == std::string::npos) {
            cut = resp.find("\r\n\r\n");
            if (cut == std::string::npos) continue;
            std::istringstream headers(resp.substr(0, cut));
            std::string version, line;
            headers >> version >> response_status;
            if (version != "HTTP/1.1" && version != "HTTP/1.0") break;
            std::getline(headers, line);
            bool have_length = false, valid = true;
            while (std::getline(headers, line)) {
                if (!line.empty() && line.back() == '\r') line.pop_back();
                auto colon = line.find(':');
                if (colon == std::string::npos) continue;
                std::string key = line.substr(0, colon);
                for (char& c : key) c = (char)std::tolower((unsigned char)c);
                if (key == "transfer-encoding") valid = false;
                if (key == "content-length") {
                    std::istringstream value(line.substr(colon + 1));
                    size_t parsed = 0; std::string extra;
                    if (!(value >> parsed) || (value >> extra) || parsed > 1024 * 1024 ||
                        (have_length && parsed != length)) valid = false;
                    length = parsed; have_length = true;
                }
            }
            // The competition server always sends Content-Length, never chunks.
            if (!valid || !have_length || response_status < 100 || response_status > 599) break;
        }
        if (resp.size() >= cut + 4 + length) {
            close(fd); status = response_status;
            return resp.substr(cut + 4, length);
        }
    }
    close(fd);
    return ""; // incomplete, timed-out or malformed response: never use its body
}
static std::string encode(const std::string&s){std::ostringstream o;for(unsigned char c:s)if(isalnum(c)||c=='-'||c=='_'||c=='.'||c=='~')o<<c;else{char h[4];std::snprintf(h,sizeof(h),"%%%02X",c);o<<h;}return o.str();}
static std::map<std::string,std::string> parse(const std::string&s){std::map<std::string,std::string>m;std::istringstream in(s);std::string l;while(std::getline(in,l)){if(!l.empty()&&l.back()=='\r')l.pop_back();size_t p=l.find(' ');m[l.substr(0,p)]=p==std::string::npos?"":l.substr(p+1);}return m;}
static std::vector<int> ints(const std::string&s){std::vector<int>v;std::istringstream in(s);int x;while(in>>x)v.push_back(x);return v;}
static int safest(int stones,const std::vector<int>&v){for(int c:v)if(c>=1&&c<=stones)return c;return 0;}

int main(int argc,char**argv){
    signal(SIGPIPE,SIG_IGN); // a dropped connection must not terminate the bot
    for(int i=1;i<argc;++i)if(std::string(argv[i])=="--selftest")return strategy::selftest();
    if(argc>=4 && std::string(argv[1])=="--exact"){
        int s=std::atoi(argv[2]),k=std::atoi(argv[3]);double seconds=argc>=5?std::atof(argv[4]):10.0;
        strategy::State p{s,{},{}};for(int c=1;c<=k&&c<=s;++c)p.me.add(c),p.opp.add(c);
        strategy::Solver sol;int best=0;uint64_t nodes=0;auto t0=strategy::Clock::now();
        int r=sol.solve_for_seconds(p,best,seconds,nodes);
        double used=std::chrono::duration<double>(strategy::Clock::now()-t0).count();
        std::cout<<"result "<<r<<" best "<<best<<" nodes "<<nodes<<" seconds "<<used<<"\n";
        return r==0?2:0;
    }
    auto env=[](const char*k,const char*d){const char*v=std::getenv(k);return std::string(v?v:d);};
    std::string server=env("CARDNIM_SERVER","http://localhost:8000"),game=env("CARDNIM_GAME",""),name=env("CARDNIM_NAME","Haowen"),seat=env("CARDNIM_SEAT","");
    for(int i=1;i+1<argc;i+=2){std::string a=argv[i];if(a=="--server")server=argv[i+1];else if(a=="--game")game=argv[i+1];else if(a=="--name")name=argv[i+1];else if(a=="--seat")seat=argv[i+1];}
    if(game.empty()){std::cerr<<"usage: haowen_bot --game ID [--server URL] [--name NAME] [--seat 1|2]\n";return 1;}
    Url u=parse_url(server);std::string base="/api/games/"+game;int status=0;
    auto joined=parse(http(u,"POST",base+"/join?format=text&name="+encode(name)+(seat.empty()?"":"&seat="+seat),"",status,5.0));
    if(status!=200){std::cerr<<"join failed ("<<status<<"): "<<joined["error"]<<"\n";return 1;}
    std::string token=joined["token"];int my_seat=std::atoi(joined["seat"].c_str());
    std::cout<<"["<<name<<"] joined "<<game<<" as seat "<<my_seat<<"\n"<<std::flush;
    if(token.empty() || (my_seat != 1 && my_seat != 2)) return 1;
    int failures=0;
    for(;;){
        auto st=parse(http(u,"GET",base+"/getstate?format=text&timeout=0.25&token="+token,"",status));
        if(status==0 || status>=500){if(++failures>=30)return 1;usleep(1000);continue;}
        if(status!=200)return 1;
        if(!st.count("status") || !st.count("your_turn") || !st.count("stones") ||
           !st.count("your_cards") || !st.count("opp_cards") || !st.count("your_time")) {
            // Finished responses only need winner/reason; validate active states below.
            if(st["status"] != "finished") {if(++failures>=30)return 1;usleep(1000);continue;}
        }
        failures=0;
        if(st["status"]=="finished"){int w=std::atoi(st["winner"].c_str());std::cout<<"["<<name<<"] "<<(w==my_seat?"WIN":"LOSS")<<": "<<st["reason"]<<"\n";return w==my_seat?0:2;}
        if(st["your_turn"]!="1")continue;
        int stones=std::atoi(st["stones"].c_str());auto mine=ints(st["your_cards"]),opp=ints(st["opp_cards"]);
        double my_time=std::atof(st["your_time"].c_str());int card=safest(stones,mine);
        if(st["status"] != "playing" || card == 0 || stones < 1 || stones > 500 ||
           !std::isfinite(my_time) || my_time <= 0) {usleep(1000);continue;}
        auto think_start = strategy::Clock::now();
        try{card=strategy::choose_card(stones,mine,opp,my_time);}catch(...){card=safest(stones,mine);}
        if(std::find(mine.begin(),mine.end(),card)==mine.end()||card<1||card>stones)card=safest(stones,mine);
        std::cout<<"["<<name<<"] "<<stones<<" stones, playing "<<card<<"\n"<<std::flush;
        double left = my_time - std::chrono::duration<double>(strategy::Clock::now()-think_start).count();
        double io_budget = std::max(0.001, std::min(0.5, left * 0.25));
        http(u,"POST",base+"/move?format=text&token="+token,"card="+std::to_string(card),status,io_budget);
        // A lost reply may follow an accepted move. Always refresh before sending again.
    }
}
