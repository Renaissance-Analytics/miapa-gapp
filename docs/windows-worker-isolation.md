# Windows worker isolation

The real authenticated zero-provider transport check uses Maipa's native
`WindowsSandbox` launcher, not the unrestricted protocol-test subprocess path.
It rereads the account's original owner/provider, active user and enabled
administrator/preparer membership before preparation, before launch and after
shutdown. A stale object never adopts a reassigned owner. The sole server-owned
installation and immutable user/account scope select a fresh AppContainer SID.
Requests cannot select native capabilities, paths, executable or environment.

The launcher creates the process suspended, assigns a non-breakaway Windows job
with `KILL_ON_JOB_CLOSE`, then verifies the actual AppContainer token/SID and job
membership before resuming. Only three explicit anonymous pipe handles are
inherited. Killing the job ends detached descendants; closing the owning server
process also closes the last job handle. Cleanup queries the real job's active
process count and removes the unique AppContainer profile. Process exit alone
never creates a verified shutdown verdict.

Each temporary capsule contains unchanged installed host/manifest/lock/package
bytes and an exact copy of the trusted Node executable. A version-manager alias
is resolved only for that server-selected executable; runtime and writable paths
reject every reparse component. Public `prepareProcessTree` runs before the
container starts. The code and prepared supervisor receive read/execute access;
only the private workspace/home receive modify access and low integrity. Their
parent grants traversal/attributes without directory listing. No ancestor,
installed package tree, server storage or foreign workspace ACL is broadened.
The worker cannot replace its runtime through a writable ancestor.

The trusted launcher validates physical paths before staging. Documented Node
`--preserve-symlinks` and `--preserve-symlinks-main` flags avoid extra module
realpath probes into inaccessible parent directories. There are no links in the
staged runtime. The app host checks assigned directory leaves without walking
server/system parents. No published package byte is patched. See the official
[Node flags](https://nodejs.org/api/cli.html#--preserve-symlinks),
[AppContainer launch](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
and [Windows jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

Required tests use synthetic files and real processes. They compare unrestricted
existence/read controls with permission-denied server/foreign reads, verify the
same restrictions in actual descendants and in two live user containers, prove
code writes fail, inspect filtered child environments, revoke scopes, fill an
input pipe, and observe pre-opened process handles after job/server death. A
request deadline covers pipe writing and response waiting. Exact runtime bytes,
real junction refusal, real published host initialization/shutdown, profile
removal and preservation of unrelated cleanup siblings are checked too.

This release enables no provider session, login, model call or network
capability. Actual provider CLI provisioning, network policy, private named-pipe
IPC, inherited provider configuration and nested published supervisor operation
remain P06/A01 gates. The synthetic descendant tests deliberately inherit the
existing anonymous output handle; they do not claim that Node's fresh global
named pipes work inside an AppContainer. A diagnostic attempt with fresh Node
child-process pipes stalled before producing a child and was terminated. Resolve
that compatibility in the actual provider integration before enabling sessions;
there is no unrestricted production fallback.
