"""5 needle+distractor tasks. Each: needle early, distractors, quiz at end.

Needles are NON-SENSITIVE fictional data (project codenames, colors,
etc.) -- models correctly refuse to disclose passwords/secrets, so
those can't be used as test needles.
"""

TASKS = [
    {
        "id": "needle-01",
        "needle": "The project codename is 'Blue Falcon'.",
        "needle_question": "What is the project codename?",
        "needle_answer": "Blue Falcon",
        "setup_turns": [
            ("user", "We're kicking off the new project. What's the codename?"),
            ("assistant", "Let me check the project charter."),
            ("tool", "Project Charter:\ncodename: Blue Falcon\nstatus: planning\nteam_size: 8"),
        ],
        "distractor_turns": 200,
        "final_question": "For the status report, what was the project codename?",
    },
    {
        "id": "needle-02",
        "needle": "The API rate limit is 5000 requests per hour per key.",
        "needle_question": "What is the API rate limit?",
        "needle_answer": "5000 requests per hour per key",
        "setup_turns": [
            ("user", "Our API calls are getting throttled. Check the rate limit docs."),
            ("assistant", "Checking rate limit documentation."),
            ("tool", "Rate Limits:\n- Free tier: 100 req/hour\n- Pro tier: 5000 requests per hour per key\n- Enterprise: unlimited"),
        ],
        "distractor_turns": 200,
        "final_question": "We're planning capacity. What was the Pro tier rate limit?",
    },
    {
        "id": "needle-03",
        "needle": "The team mascot is a rubber duck named 'Quackers'.",
        "needle_question": "What is the team mascot?",
        "needle_answer": "a rubber duck named Quackers",
        "setup_turns": [
            ("user", "What's our team mascot for the hackathon?"),
            ("assistant", "Checking the team wiki."),
            ("tool", "Team Wiki:\nmascot: rubber duck named 'Quackers'\ncolor: yellow\nadopted: 2024-03-15"),
        ],
        "distractor_turns": 200,
        "final_question": "For the hackathon banner, what was our team mascot?",
    },
    {
        "id": "needle-04",
        "needle": "The TLS certificate expires on 2026-12-01 and must be renewed by Nov 15.",
        "needle_question": "When does the TLS cert expire and when must it be renewed?",
        "needle_answer": "expires 2026-12-01, renew by Nov 15",
        "setup_turns": [
            ("user", "Check when our TLS cert expires."),
            ("assistant", "Checking certificate expiry."),
            ("tool", "Certificate:\nsubject: *.example.com\nexpires: 2026-12-01\nrenew_by: 2026-11-15\nissuer: LetsEncrypt"),
        ],
        "distractor_turns": 200,
        "final_question": "Planning the maintenance window. When does the TLS cert expire and what's the renew-by date?",
    },
    {
        "id": "needle-05",
        "needle": "The office plant is a ficus named 'Fernando'.",
        "needle_question": "What kind of plant is in the office and what's its name?",
        "needle_answer": "ficus named Fernando",
        "setup_turns": [
            ("user", "What's that plant in the corner of the office?"),
            ("assistant", "Checking the office inventory."),
            ("tool", "Office Inventory:\nplant: ficus\nname: 'Fernando'\nlocation: corner by window\nwatering: weekly"),
        ],
        "distractor_turns": 200,
        "final_question": "The plant looks droopy. What kind is it and what's its name?",
    },
]


def generate_distractors(n: int, seed: int = 42) -> list[tuple[str, str, str]]:
    """Generate n filler turns of plausible-but-irrelevant tool output.

    Each distractor is ~200 tokens of verbose output, so 200 distractors
    ≈ 40k tokens -- enough to trigger pruning under typical budgets.
    """
    import random
    rng = random.Random(seed)
    topics = [
        ("Checking disk usage.",
         "Filesystem Report (detailed):\n"
         "/dev/sda1: 45% used (210GB of 466GB), mounted on /\n"
         "/dev/sdb1: 12% used (56GB of 466GB), mounted on /data\n"
         "/dev/sdc1: 78% used (364GB of 466GB), mounted on /backup\n"
         "Inodes: sda1 12% used, sdb1 3% used, sdc1 45% used\n"
         "Largest directories on /:\n"
         "  /var/log: 45GB (rotated weekly, 4 weeks retained)\n"
         "  /opt/app: 32GB (application binaries and assets)\n"
         "  /tmp: 2GB (cleaned on reboot)\n"
         "Recommendation: sdc1 approaching threshold, consider archival."),
        ("Checking service status.",
         "Service Status (systemd):\n"
         "nginx: active (running) since Mon 2026-10-05 08:12:33 UTC; 5 days ago\n"
         "  Main PID: 1234, Tasks: 8, Memory: 45.2M\n"
         "postgres: active (running) since Mon 2026-10-05 08:12:35 UTC\n"
         "  Main PID: 5678, Tasks: 12, Memory: 1.2G\n"
         "redis: active (running) since Mon 2026-10-05 08:12:36 UTC\n"
         "  Main PID: 9012, Tasks: 5, Memory: 256M\n"
         "Failed units: 0\n"
         "Uptime: 5 days, 3 hours, 22 minutes"),
        ("Tailing application logs.",
         "Application Logs (last 100 lines, sampled):\n"
         "[2026-10-10 06:45:12 INFO] request completed: GET /api/v1/users in 45ms (200)\n"
         "[2026-10-10 06:45:13 INFO] request completed: POST /api/v1/orders in 52ms (201)\n"
         "[2026-10-10 06:45:14 WARN] slow query detected: SELECT * FROM orders WHERE created_at > $1 (412ms)\n"
         "[2026-10-10 06:45:15 INFO] cache hit: user:12345 (0.3ms)\n"
         "[2026-10-10 06:45:16 INFO] request completed: GET /api/v1/products in 38ms (200)\n"
         "[2026-10-10 06:45:17 ERROR] failed to send email: SMTP timeout after 30s (retry 1/3)\n"
         "[2026-10-10 06:45:18 INFO] background job completed: generate_daily_report (12.4s)\n"
         "... (93 more lines omitted for brevity) ...\n"
         "Summary: 89 INFO, 8 WARN, 3 ERROR in last 5 minutes"),
        ("Checking network statistics.",
         "Network Interface Statistics:\n"
         "eth0: RX 1.2 Gbps, TX 890 Mbps, errors 0, dropped 12\n"
         "  latency p50: 4ms, p95: 9ms, p99: 12ms, packet loss: 0%\n"
         "eth1: RX 45 Mbps, TX 12 Mbps (management network)\n"
         "Active connections: 1,234 (892 ESTABLISHED, 342 TIME_WAIT)\n"
         "Top talkers (last hour):\n"
         "  10.0.1.50: 45GB (app server)\n"
         "  10.0.1.51: 32GB (app server)\n"
         "  10.0.2.10: 12GB (database replication)"),
        ("Listing processes.",
         "Process List (top by CPU):\n"
         "PID 1234: nginx (2.1% CPU, 45M RAM, running 5 days)\n"
         "PID 5678: postgres (5.3% CPU, 1.2G RAM, running 5 days)\n"
         "PID 9012: redis-server (0.8% CPU, 256M RAM)\n"
         "PID 3456: python3 app.py (12.4% CPU, 890M RAM, 12 threads)\n"
         "PID 7890: node worker.js (3.2% CPU, 234M RAM)\n"
         "Total processes: 142, load average: 1.23, 1.45, 1.67"),
        ("Checking memory usage.",
         "Memory Report:\n"
         "Total: 64GB, Used: 38GB (59%), Free: 26GB\n"
         "Buffers: 2.1GB, Cached: 12.4GB, Swap: 0GB (disabled)\n"
         "Top consumers:\n"
         "  postgres: 1.2GB (shared buffers + connections)\n"
         "  python3 app.py: 890MB (application + caches)\n"
         "  redis: 256M (in-memory dataset)\n"
         "  nginx: 45M (worker processes)\n"
         "No OOM kills in last 30 days. Memory pressure: normal."),
    ]
    out = []
    for i in range(n):
        prompt, output = rng.choice(topics)
        out.append(("user", f"Distractor check {i+1}: {prompt.lower()}"))
        out.append(("assistant", f"Running diagnostic: {prompt}"))
        out.append(("tool", f"{output}\n[diagnostic {i+1} of {n} completed]"))
    return out
