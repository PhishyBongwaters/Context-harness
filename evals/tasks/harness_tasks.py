"""5 needle tasks about the real harness codebase.

Each task: the model investigates the harness repo, encounters a specific
fact naturally via tool calls, then gets quizzed on it after context growth.
"""

TASKS = [
    {
        "id": "harness-01",
        "needle_answer": "run_turn",
        "turn1": (
            "Read harness/loop.py and find the Loop class. "
            "What is the name of the method that runs a single agent turn?"),
        "followups": [
            "Now read harness/deterministic.py. What does prune_deterministic do?",
            "Read harness/approvals.py. What is the Policy class for?",
            "Read harness/session.py. What does init_layout create?",
        ],
        "final_question": (
            "Back to the Loop class in harness/loop.py: "
            "what is the name of the method that runs a single agent turn?"),
    },
    {
        "id": "harness-02",
        "needle_answer": "5",
        "turn1": (
            "Read harness/config.py and find the prune_keep_tools setting. "
            "What is its default value?"),
        "followups": [
            "Read harness/loop.py. How does the Loop use prune_keep_tools?",
            "Read harness/deterministic.py. Where is keep_recent_tools used?",
            "List the files in harness/ and summarize what each does.",
        ],
        "final_question": (
            "What is the default value of prune_keep_tools in harness/config.py? "
            "Just give the number."),
    },
    {
        "id": "harness-03",
        "needle_answer": "history.md",
        "turn1": (
            "Read harness/session.py and find the HISTORY_TEMPLATE. "
            "What is the name of the file that stores the curated transcript?"),
        "followups": [
            "Read harness/assembly.py. What does the assemble function do?",
            "Read harness/loop.py. How does _transcript_messages work?",
            "Check what test files exist in tests/ related to sessions.",
        ],
        "final_question": (
            "What is the filename for the curated durable transcript "
            "in the session layout?"),
    },
    {
        "id": "harness-04",
        "needle_answer": "init_layout",
        "turn1": (
            "Read harness/session.py. What is the name of the function "
            "that creates the blank-slate session layout?"),
        "followups": [
            "Read harness/loop.py. Where is init_layout called from?",
            "Check the SAT_FILES dict in session.py. What satellite files are defined?",
            "Read the INDEX_TEMPLATE. What does it contain?",
        ],
        "final_question": (
            "What is the name of the function in harness/session.py "
            "that creates the session directory layout?"),
    },
    {
        "id": "harness-05",
        "needle_answer": "hypotheses",
        "turn1": (
            "Check the sats/ directory structure. What is the name of the "
            "satellite file for half-formed ideas and unproven suspicions? "
            "(Just the name without .md)"),
        "followups": [
            "Read harness/loop.py. How does the system prompt describe the sats?",
            "Check git log for when hypotheses.md was added.",
            "Read docs/satellite-context-proposal.md. What are the four satellites?",
        ],
        "final_question": (
            "What is the satellite file called (without .md extension) "
            "for half-formed ideas and unproven suspicions?"),
    },
]
