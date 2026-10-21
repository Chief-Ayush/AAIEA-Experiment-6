"""A small in-memory imitation of the GitHub REST API (the endpoints ReviewPilot uses), served by uvicorn in a thread.

It enforces the rules that matter for correctness: fast-forward-only ref updates, review comments only on lines in the diff,
and "cannot approve / request changes on your own pull request".
"""
from __future__ import annotations

import difflib
import hashlib
import io
import tarfile
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

LOGIN = __import__("os").environ.get("FAKE_GH_LOGIN", "tester")


def _sha(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode() + str(time.time_ns()).encode()).hexdigest()


class Store:
    def __init__(self):
        self.repos: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.reviews: list[dict] = []

    # ---------------------------------------------------------------- git objects
    def new_repo(self, owner, name, private=True, description=""):
        full = f"{owner}/{name}"
        r = {"full_name": full, "name": name, "owner": owner, "private": private, "description": description, "default_branch": "main",
             "refs": {}, "commits": {}, "trees": {}, "blobs": {}, "pulls": {}, "next_pr": 1, "pushed_at": "2026-10-21T10:00:00Z"}
        self.repos[full] = r
        tree = self.put_tree(r, {"README.md": self.put_blob(r, f"# {name}\n")})
        r["refs"]["main"] = self.put_commit(r, tree, [], "Initial commit")
        return r

    def put_blob(self, r, content: str) -> str:
        s = hashlib.sha1(content.encode()).hexdigest()
        r["blobs"][s] = content
        return s

    def put_tree(self, r, entries: dict) -> str:
        s = _sha("tree", sorted(entries.items()))
        r["trees"][s] = dict(entries)
        return s

    def put_commit(self, r, tree, parents, message) -> str:
        s = _sha("commit", tree, parents, message)
        r["commits"][s] = {"tree": tree, "parents": list(parents), "message": message}
        return s

    def files_at(self, r, sha) -> dict[str, str]:
        return {p: r["blobs"][b] for p, b in r["trees"][r["commits"][sha]["tree"]].items()}

    def ancestors(self, r, sha) -> list[str]:
        out, todo = [], [sha]
        while todo:
            s = todo.pop(0)
            if s in out:
                continue
            out.append(s)
            todo += r["commits"][s]["parents"]
        return out

    def merge_base(self, r, a, b) -> str:
        anc = set(self.ancestors(r, a))
        return next(s for s in self.ancestors(r, b) if s in anc)


S = Store()


def _repo(req: Request) -> dict:
    full = f"{req.path_params['owner']}/{req.path_params['name']}"
    if full not in S.repos:
        raise KeyError(full)
    return S.repos[full]


def nf(msg="Not Found"):
    return JSONResponse({"message": msg}, status_code=404)


def _repo_json(r):
    return {"full_name": r["full_name"], "name": r["name"], "private": r["private"], "default_branch": r["default_branch"],
            "html_url": f"https://github.test/{r['full_name']}", "description": r["description"], "pushed_at": r["pushed_at"], "updated_at": r["pushed_at"]}


async def user(req):
    return JSONResponse({"login": LOGIN})


async def user_repos(req):
    if req.method == "POST":
        b = await req.json()
        full = f"{LOGIN}/{b['name']}"
        if full in S.repos:
            return JSONResponse({"message": "name already exists"}, status_code=422)
        return JSONResponse(_repo_json(S.new_repo(LOGIN, b["name"], b.get("private", True), b.get("description", ""))), status_code=201)
    if int(req.query_params.get("page", 1)) > 1:
        return JSONResponse([])
    return JSONResponse([_repo_json(r) for r in S.repos.values()])


async def repo(req):
    try:
        return JSONResponse(_repo_json(_repo(req)))
    except KeyError:
        return nf()


async def get_ref(req):
    try:
        r = _repo(req)
    except KeyError:
        return nf()
    b = req.path_params["branch"]
    if b not in r["refs"]:
        return nf()
    return JSONResponse({"ref": f"refs/heads/{b}", "object": {"sha": r["refs"][b], "type": "commit"}})


async def create_ref(req):
    r = _repo(req)
    b = await req.json()
    name = b["ref"].removeprefix("refs/heads/")
    if name in r["refs"]:
        return JSONResponse({"message": "Reference already exists"}, status_code=422)
    r["refs"][name] = b["sha"]
    return JSONResponse({"ref": b["ref"], "object": {"sha": b["sha"]}}, status_code=201)


async def update_ref(req):
    r = _repo(req)
    b = await req.json()
    name = req.path_params["branch"]
    old = r["refs"].get(name)
    if old is None:
        return nf()
    if not b.get("force") and old not in S.ancestors(r, b["sha"]):
        return JSONResponse({"message": "Update is not a fast forward"}, status_code=422)
    r["refs"][name] = b["sha"]
    return JSONResponse({"ref": f"refs/heads/{name}", "object": {"sha": b["sha"]}})


async def get_commit(req):
    r = _repo(req)
    s = req.path_params["sha"]
    if s not in r["commits"]:
        return nf()
    return JSONResponse({"sha": s, "tree": {"sha": r["commits"][s]["tree"]}, "message": r["commits"][s]["message"]})


async def blobs(req):
    r = _repo(req)
    b = await req.json()
    return JSONResponse({"sha": S.put_blob(r, b["content"])}, status_code=201)


async def trees(req):
    r = _repo(req)
    b = await req.json()
    entries = dict(r["trees"].get(b.get("base_tree"), {}))
    for e in b["tree"]:
        if e.get("sha") is None:
            entries.pop(e["path"], None)
        else:
            entries[e["path"]] = e["sha"]
    return JSONResponse({"sha": S.put_tree(r, entries)}, status_code=201)


async def commits(req):
    r = _repo(req)
    b = await req.json()
    return JSONResponse({"sha": S.put_commit(r, b["tree"], b["parents"], b["message"])}, status_code=201)


async def tarball(req):
    r = _repo(req)
    return RedirectResponse(f"/_codeload/{r['full_name']}/{req.path_params['ref']}", status_code=302)


async def codeload(req):
    r = _repo(req)
    ref = req.path_params["ref"]
    sha = r["refs"].get(ref, ref)
    if sha not in r["commits"]:
        return nf()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in S.files_at(r, sha).items():
            data = content.encode()
            info = tarfile.TarInfo(f"{r['owner']}-{r['name']}-{sha[:7]}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return Response(buf.getvalue(), media_type="application/x-gzip")


async def compare(req):
    r = _repo(req)
    base, head = req.path_params["spec"].split("...")
    return JSONResponse({"merge_base_commit": {"sha": S.merge_base(r, r["refs"].get(base, base), r["refs"].get(head, head))}})


def _pr_json(r, n):
    p = r["pulls"][n]
    head_sha = r["refs"][p["head"]]
    base_sha = r["refs"][p["base"]]
    return {"number": n, "title": p["title"], "body": p["body"], "state": "open", "draft": False, "user": {"login": p["author"]},
            "html_url": f"https://github.test/{r['full_name']}/pull/{n}", "updated_at": "2026-10-21T10:00:00Z",
            "head": {"ref": p["head"], "sha": head_sha, "repo": {"full_name": r["full_name"]}}, "base": {"ref": p["base"], "sha": base_sha}}


def _diff(r, n):
    p = r["pulls"][n]
    head = r["refs"][p["head"]]
    mb = S.merge_base(r, r["refs"][p["base"]], head)
    a, b = S.files_at(r, mb), S.files_at(r, head)
    out = []
    for path in sorted(set(a) | set(b)):
        if a.get(path) == b.get(path):
            continue
        status = "added" if path not in a else "removed" if path not in b else "modified"
        add = dele = 0
        for line in difflib.unified_diff((a.get(path) or "").splitlines(), (b.get(path) or "").splitlines(), lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                add += 1
            elif line.startswith("-") and not line.startswith("---"):
                dele += 1
        added_lines = set()
        sm = difflib.SequenceMatcher(a=(a.get(path) or "").splitlines(), b=(b.get(path) or "").splitlines(), autojunk=False)
        for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
            if tag in ("insert", "replace"):
                added_lines.update(range(j1 + 1, j2 + 1))
        out.append({"filename": path, "status": status, "additions": add, "deletions": dele, "_added": added_lines})
    return out, mb, head


async def pulls(req):
    r = _repo(req)
    if req.method == "POST":
        b = await req.json()
        if b["head"] not in r["refs"]:
            return JSONResponse({"message": "Validation Failed", "errors": [{"message": "head not found"}]}, status_code=422)
        n = r["next_pr"]
        r["next_pr"] += 1
        r["pulls"][n] = {"title": b["title"], "body": b.get("body", ""), "head": b["head"], "base": b["base"], "author": LOGIN}
        return JSONResponse(_pr_json(r, n), status_code=201)
    if int(req.query_params.get("page", 1)) > 1:
        return JSONResponse([])
    head = req.query_params.get("head")
    items = [_pr_json(r, n) for n in sorted(r["pulls"], reverse=True)]
    if head:
        items = [x for x in items if f"{r['owner']}:{x['head']['ref']}" == head]
    return JSONResponse(items)


async def pull(req):
    r = _repo(req)
    n = int(req.path_params["n"])
    return JSONResponse(_pr_json(r, n)) if n in r["pulls"] else nf()


async def pull_files(req):
    r = _repo(req)
    if int(req.query_params.get("page", 1)) > 1:
        return JSONResponse([])
    files, _, _ = _diff(r, int(req.path_params["n"]))
    return JSONResponse([{k: v for k, v in f.items() if not k.startswith("_")} for f in files])


async def pull_commits(req):
    r = _repo(req)
    if int(req.query_params.get("page", 1)) > 1:
        return JSONResponse([])
    _, mb, head = _diff(r, int(req.path_params["n"]))
    shas = [s for s in S.ancestors(r, head) if s not in set(S.ancestors(r, mb))]
    return JSONResponse([{"sha": s, "commit": {"message": r["commits"][s]["message"]}} for s in reversed(shas)])


async def reviews(req):
    r = _repo(req)
    n = int(req.path_params["n"])
    b = await req.json()
    p = r["pulls"][n]
    if b["event"] in ("APPROVE", "REQUEST_CHANGES") and p["author"] == LOGIN:
        return JSONResponse({"message": "Unprocessable Entity", "errors": ["Can not approve your own pull request"]}, status_code=422)
    files, _, _ = _diff(r, n)
    added = {f["filename"]: f["_added"] for f in files}
    for c in b.get("comments", []):
        if c["line"] not in added.get(c["path"], set()):
            return JSONResponse({"message": "Unprocessable Entity", "errors": ["Line could not be resolved"]}, status_code=422)
    rid = len(S.reviews) + 1
    S.reviews.append({"repo": r["full_name"], "number": n, **b})
    return JSONResponse({"id": rid, "html_url": f"https://github.test/{r['full_name']}/pull/{n}#pullrequestreview-{rid}"})


P = "/repos/{owner}/{name}"
app = Starlette(routes=[
    Route("/user", user), Route("/user/repos", user_repos, methods=["GET", "POST"]), Route(P, repo),
    Route(P + "/git/ref/heads/{branch:path}", get_ref), Route(P + "/git/refs", create_ref, methods=["POST"]),
    Route(P + "/git/refs/heads/{branch:path}", update_ref, methods=["PATCH"]), Route(P + "/git/commits/{sha}", get_commit),
    Route(P + "/git/blobs", blobs, methods=["POST"]), Route(P + "/git/trees", trees, methods=["POST"]),
    Route(P + "/git/commits", commits, methods=["POST"]), Route(P + "/tarball/{ref}", tarball),
    Route("/_codeload/{owner}/{name}/{ref}", codeload), Route(P + "/compare/{spec}", compare),
    Route(P + "/pulls", pulls, methods=["GET", "POST"]), Route(P + "/pulls/{n:int}", pull), Route(P + "/pulls/{n:int}/files", pull_files),
    Route(P + "/pulls/{n:int}/commits", pull_commits), Route(P + "/pulls/{n:int}/reviews", reviews, methods=["POST"]),
])


def serve(port: int = 0) -> tuple[str, uvicorn.Server]:
    import socket
    if not port:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server
