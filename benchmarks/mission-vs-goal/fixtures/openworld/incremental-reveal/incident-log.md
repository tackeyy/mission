# Incident log: checkout failures on 2026-05-14

Chronological log. Read to the end before concluding a root cause.

## 01:10 — first alerts

Checkout success rate drops from 99.4% to 71%. Errors are `502` from the
gateway. The most recent deploy at 01:05 shipped a change to the payment
serializer, so the on-call's first hypothesis is that the payment serializer
change is the cause.

## 01:18 — serializer looks guilty

The serializer change touched the exact request path that is now failing. Rolling
it back looks like the obvious fix. A rollback is started.

## 01:34 — rollback did not help

The serializer rollback completes at 01:31. Success rate stays at 71%. The
serializer was not the cause; the failures continue with the old serializer.

## 01:52 — a second signal

Connection-pool saturation warnings appear in the worker logs starting at 01:02,
three minutes *before* the 01:05 deploy. The pool was already saturating before
any code shipped. This points away from the deploy entirely.

## 02:45 — definitive evidence

The database team confirms a runaway migration job started at 01:00 that held an
exclusive lock on the `orders` table and exhausted the connection pool. Killing
the migration job at 02:44 restores the success rate to 99.4% within one minute.
The root cause is the runaway migration job holding the exclusive lock, not the
serializer deploy. The `02:45` entry is the final arbiter.
