# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Server-side payroll services: permissions, workflow and readiness.

These modules hold the decision logic that used to live inline in ``api.py``.
Everything here is server-authoritative — the dashboard renders what these return
but never decides eligibility itself.
"""
