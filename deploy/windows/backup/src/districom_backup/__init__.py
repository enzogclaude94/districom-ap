"""DistriCom backup proof of concept for Windows (W2-A, ADR 016 proposed).

Physical backups with PostgreSQL's own tools, because pgBackRest has no Windows version:
WAL archiving into a local repository (``repo1``), verified base backups, point-in-time
recovery, gap detection across timelines, an encrypted off-site copy with rclone crypt and
retention that keeps everything a retained base backup needs.

Isolated on purpose: nothing in the application or in the Linux deployment uses it.
"""
