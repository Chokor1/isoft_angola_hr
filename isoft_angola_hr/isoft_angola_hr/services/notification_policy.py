# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The application's notification policy. There is exactly one, and it lives here.

**isoft_angola_hr does not notify anybody.** It does not write to the bell menu, it does
not send e-mail, and it registers no scheduled job or document event whose purpose is to
tell somebody something. Work waiting for a person is shown on the screen they open —
the HR inbox, the approvals list, the expiry panels — which is a pull, not a push.

Why a module constant rather than a setting
-------------------------------------------
A checkbox in Isoft HR Settings would put this one click away from being switched back on
by somebody who did not mean to, and the switch would look like an ordinary preference
rather than a decision. The policy is a deliberate, permanent property of the build, so it
is expressed where the build is: in code, reviewable in a diff.

Where the guard sits
--------------------
Not in twenty producers. Every notification this application has ever sent passes through
one of three writers, and all three ask this module first:

* :func:`hr_notifications._notify` — the only code that creates a Notification Log row
* :func:`hr_notifications._tell`   — the only code that calls ``frappe.sendmail``
* :func:`isoft_attendance_occurrence._notify_hr` — the one writer that predates
  ``hr_notifications`` and still inserts its own Notification Log

A producer added tomorrow is silenced automatically if it goes through those, and
``test_notifications_disabled.TestNoUnguardedWriter`` fails the build if it does not.

What this deliberately does NOT touch
-------------------------------------
* ``frappe.msgprint`` / ``frappe.throw`` — validation, refusals and warnings about the
  action the caller just performed. A person who presses Approve and may not is told so.
* ``frappe.show_alert`` / ``SS.toast`` — "Saved", "Created". Confirmation of your own act.
* The HR inbox, approvals list, contract-expiry and probation panels — screens, not messages.
* Frappe's and ERPNext's own notifications. This policy is scoped to one app; the
  Notification Log table is shared, and other applications keep using it normally.
"""

#: The policy. Notifications produced by this application are off, permanently.
#: Flipping this to True restores every producer listed in the module docstring; nothing
#: else in the application reads it, and nothing writes it.
HR_NOTIFICATIONS_ENABLED = False


def notifications_enabled():
    """Whether this application may notify anybody. Read it, do not cache it.

    A function rather than a bare import of the constant so that the three writers have a
    single call to make, and so a test can substitute the policy without reaching into
    another module's globals.
    """
    return bool(HR_NOTIFICATIONS_ENABLED)


def suppressed():
    """The form the writers actually use — ``if suppressed(): return 0``."""
    return not notifications_enabled()
