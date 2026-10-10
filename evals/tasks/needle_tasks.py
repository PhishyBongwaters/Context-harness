"""5 needle+distractor tasks. Each: needle early, distractors, quiz at end."""

TASKS = [
    {
        "id": "needle-01",
        "needle": "The production database password is 'correct-horse-battery-staple-2026'.",
        "needle_question": "What is the production database password?",
        "needle_answer": "correct-horse-battery-staple-2026",
        "setup_turns": [
            ("user", "We're debugging the production outage. First, let me check the db config."),
            ("assistant", "I'll check the database configuration."),
            ("tool", "Config dump:\ndb_host=prod-db-01.internal\ndb_password=correct-horse-battery-staple-2026\ndb_port=5432"),
        ],
        "distractor_turns": 20,  # generated filler tool outputs
        "final_question": "The outage is fixed. For the postmortem, what was the production database password?",
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
        "distractor_turns": 20,
        "final_question": "We're planning capacity. What was the Pro tier rate limit?",
    },
    {
        "id": "needle-03",
        "needle": "The backup server is at 10.0.4.99, SSH port 2222.",
        "needle_question": "Where is the backup server and what port?",
        "needle_answer": "10.0.4.99 port 2222",
        "setup_turns": [
            ("user", "Need to verify backups are running. Where's the backup server?"),
            ("assistant", "Looking up backup server details."),
            ("tool", "Infrastructure:\nbackup-server: 10.0.4.99\nssh_port: 2222\nschedule: daily 02:00 UTC"),
        ],
        "distractor_turns": 20,
        "final_question": "Backups failed last night. What's the backup server address and SSH port?",
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
        "distractor_turns": 20,
        "final_question": "Planning the maintenance window. When does the TLS cert expire and what's the renew-by date?",
    },
    {
        "id": "needle-05",
        "needle": "The on-call engineer is Priya Sharma, reachable at priya@example.com.",
        "needle_question": "Who is on-call and how to reach them?",
        "needle_answer": "Priya Sharma, priya@example.com",
        "setup_turns": [
            ("user", "Who's on-call this week?"),
            ("assistant", "Checking the on-call rotation."),
            ("tool", "On-call schedule:\nWeek of Oct 5: Priya Sharma <priya@example.com>\nWeek of Oct 12: Marcus Chen\nEscalation: page #incident-response"),
        ],
        "distractor_turns": 20,
        "final_question": "We have a P1 incident. Who's on-call and what's their contact?",
    },
]


def generate_distractors(n: int, seed: int = 42) -> list[tuple[str, str, str]]:
    """Generate n filler turns of plausible-but-irrelevant tool output."""
    import random
    rng = random.Random(seed)
    topics = [
        ("Checking disk usage.", "Filesystem:\n/dev/sda1 45% used\n/dev/sdb1 12% used"),
        ("Checking service status.", "Services:\nnginx: running\npostgres: running\nredis: running"),
        ("Tailing logs.", "Logs (last 50 lines):\n[INFO] request completed in 45ms\n[INFO] request completed in 52ms\n[WARN] slow query detected"),
        ("Checking network.", "Network:\neth0: 1.2 Gbps\nlatency p99: 12ms\npacket loss: 0%"),
        ("Listing processes.", "Processes:\nPID 1234: nginx (2.1% CPU)\nPID 5678: postgres (5.3% CPU)"),
        ("Checking memory.", "Memory:\nTotal: 64GB\nUsed: 38GB (59%)\nSwap: 0GB"),
    ]
    out = []
    for i in range(n):
        prompt, output = rng.choice(topics)
        out.append(("user", f"Distractor check {i+1}: {prompt.lower()}"))
        out.append(("assistant", f"Running: {prompt}"))
        out.append(("tool", f"{output}\n[check {i+1} of {n}]"))
    return out
