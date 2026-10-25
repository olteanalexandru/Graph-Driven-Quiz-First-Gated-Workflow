---
paths:
  - "supabase/**"
---
# Supabase rules
- Every new table enables row level security in the same migration and gets its policies there.
- Views use `with (security_invoker = true)`; SECURITY DEFINER functions set `search_path` explicitly and revoke EXECUTE from anon when it is not needed.
- Each new or changed policy gets a two-user test (user B cannot read or change user A's rows; anonymous cannot read private rows).
- The secret key (sb_secret_..., or legacy service_role) is read only in server-only modules.
- Server code authorizes with auth.getClaims() or auth.getUser(), never auth.getSession().
