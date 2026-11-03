"""Publicador en la nube (GitHub Actions) para Instagram y Facebook: no depende de ninguna Mac.

La Mac deja en agenda/meta.json lo aprobado (con URLs públicas); esto publica lo que ya toca, cada 15 min,
y anota el resultado en agenda/hecho.json (la Mac lo lee para marcarlo publicado). Solo librería estándar."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

V = "v21.0"
G = f"https://graph.facebook.com/{V}"
TOK = os.environ.get("META_SU_TOKEN", "")
MX = timezone(timedelta(hours=-6))
AGENDA, HECHO = "agenda/meta.json", "agenda/hecho.json"
MAX_RETRASO_H = 12      # si algo se atrasó más que esto, no se publica fuera de hora
MAX_INTENTOS = 3


def req(url, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    r = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode(errors='ignore')[:400]}") from None


def esperar(cid, intentos=60):
    for _ in range(intentos):
        st = req(f"{G}/{cid}?fields=status_code,status&access_token={TOK}")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"procesamiento: {st.get('status')}")
        time.sleep(10)
    raise RuntimeError("no terminó de procesarse en 10 min")


def page_token(page):
    return req(f"{G}/{page}?fields=access_token&access_token={TOK}")["access_token"]


def ig(e):
    base, kind, media = f"{G}/{e['target']}/media", e["kind"], e["media"]
    if kind == "reel" and not e.get("cover"):  # un reel del feed nunca sale sin portada (3-nov)
        raise RuntimeError("sin portada: la Mac la vuelve a generar")
    if kind in ("reel", "trial"):
        p = {"media_type": "REELS", "video_url": media[0], "caption": e.get("caption", ""), "access_token": TOK}
        if e.get("cover"):
            p["cover_url"] = e["cover"]
        if kind == "trial" and os.path.exists("agenda/trial_off"):
            raise RuntimeError("trials apagados: no se publica (nunca como reel normal)")
        if kind == "trial":  # Trial Reel: primero a quien no te sigue; se gradúa solo si rinde
            p["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE"})
            p["share_to_feed"] = "false"  # un trial NO va al feed de tus seguidores
        else:
            p["share_to_feed"] = "true"
        if e.get("audio"):  # música oficial de Instagram bajo la voz
            p["audio_configuration"] = json.dumps(e["audio"])
        try:
            c = req(base, p)
        except RuntimeError:
            if not e.get("audio"):
                raise
            p.pop("audio_configuration")  # si la pista ya no está disponible, sale con sonido original
            c = req(base, p)
    elif kind == "story":
        key = "video_url" if media[0].endswith(".mp4") else "image_url"
        c = req(base, {"media_type": "STORIES", key: media[0], "access_token": TOK})
    elif len(media) == 1:
        c = req(base, {"image_url": media[0], "caption": e.get("caption", ""), "access_token": TOK})
    else:
        kids = [req(base, {"image_url": u, "is_carousel_item": "true", "access_token": TOK})["id"] for u in media[:10]]
        c = req(base, {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": e.get("caption", ""),
                       "access_token": TOK})
    esperar(c["id"])
    pub = req(f"{G}/{e['target']}/media_publish", {"creation_id": c["id"], "access_token": TOK})["id"]
    if e.get("first_comment"):
        try:
            req(f"{G}/{pub}/comments", {"message": e["first_comment"], "access_token": TOK})
        except RuntimeError:
            pass  # el post ya salió; el comentario es extra
    return pub


def fb(e):
    page, media = e["target"], e["media"]
    pt = page_token(page)
    if e["kind"] in ("reel", "trial"):
        s = req(f"{G}/{page}/video_reels", {"upload_phase": "start", "access_token": pt})
        req(f"https://rupload.facebook.com/video-upload/{V}/{s['video_id']}", b"",
            {"Authorization": f"OAuth {pt}", "file_url": media[0]}, "POST")
        req(f"{G}/{page}/video_reels", {"upload_phase": "finish", "video_id": s["video_id"], "video_state": "PUBLISHED",
                                        "description": e.get("caption", ""), "access_token": pt})
        return s["video_id"]
    if len(media) == 1:
        return req(f"{G}/{page}/photos", {"url": media[0], "caption": e.get("caption", ""), "access_token": pt})["id"]
    ids = [req(f"{G}/{page}/photos", {"url": u, "published": "false", "access_token": pt})["id"] for u in media[:10]]
    data = {"message": e.get("caption", ""), "access_token": pt}
    data.update({f"attached_media[{i}]": json.dumps({"media_fbid": x}) for i, x in enumerate(ids)})
    return req(f"{G}/{page}/feed", data)["id"]


def _git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True)


def hecho_remoto():
    """Lo publicado según main AHORA (no según el checkout: una corrida en espera trae una copia vieja y así salió
    doble el post del descanso el 2-nov)."""
    if _git("fetch", "-q", "origin", "main").returncode != 0:
        return None
    r = _git("show", f"origin/main:{HECHO}")
    try:
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return {}


def _mejor(a, b):
    return a if (a or {}).get("ok") or not (b or {}).get("ok") else b


def guardar(hecho):
    """Guarda en main en cuanto algo sale (fusiona con lo que haya; nunca pierde un 'publicado')."""
    for _ in range(6):
        rem = hecho_remoto()
        if rem is None:
            time.sleep(5)
            continue
        for k, v in rem.items():
            hecho[k] = _mejor(hecho.get(k), v) if k in hecho else v
        _git("reset", "-q", "--hard", "origin/main")
        os.makedirs("agenda", exist_ok=True)
        json.dump(hecho, open(HECHO, "w"), ensure_ascii=False, indent=1)
        _git("add", HECHO)
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return True
        _git("-c", "user.name=publicador", "-c", "user.email=publicador@users.noreply.github.com",
             "commit", "-qm", f"publicado {datetime.now(MX).isoformat(timespec='seconds')}")
        if _git("push", "-q", "origin", "HEAD:main").returncode == 0:
            return True
        time.sleep(4)
    return False


def ya_en_instagram(e):
    """Segunda llave: si en las últimas 6 h ya hay en la cuenta un post con el mismo texto y del mismo tipo, no se
    vuelve a subir (se anota ese). Cubre cualquier pérdida de estado."""
    if e["net"] != "ig" or e["kind"] == "story":
        return None
    try:
        d = req(f"{G}/{e['target']}/media?fields=id,caption,timestamp,media_product_type,is_shared_to_feed"
                f"&limit=15&access_token={TOK}")
    except RuntimeError:
        return None
    cap = " ".join((e.get("caption") or "").split())[:120]
    if not cap:
        return None
    limite = datetime.now(timezone.utc) - timedelta(hours=6)
    for m in d.get("data", []):
        t = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if t < limite or " ".join((m.get("caption") or "").split())[:120] != cap:
            continue
        tipo = m.get("media_product_type")
        if e["kind"] == "trial" and tipo == "REELS" and m.get("is_shared_to_feed") is False:
            return m["id"]
        if e["kind"] == "reel" and tipo == "REELS" and m.get("is_shared_to_feed") is not False:
            return m["id"]
        if e["kind"] == "images" and tipo == "FEED":
            return m["id"]
    return None


def main():
    if not TOK:
        print("falta el secreto META_SU_TOKEN: corre activar_publicador.sh en la Mac")
        return
    agenda = json.load(open(AGENDA)) if os.path.exists(AGENDA) else []
    hecho = json.load(open(HECHO)) if os.path.exists(HECHO) else {}
    rem = hecho_remoto()
    if rem is not None:
        hecho = {k: _mejor(hecho.get(k), v) for k, v in rem.items()} | {k: v for k, v in hecho.items() if k not in rem}
    if _git("show", f"origin/main:{AGENDA}").returncode == 0:  # y la agenda más nueva
        try:
            agenda = json.loads(_git("show", f"origin/main:{AGENDA}").stdout)
        except ValueError:
            pass
    ahora = datetime.now(MX).replace(tzinfo=None)
    for e in sorted(agenda, key=lambda x: (x["when"], bool(x.get("despues_de")))):
        h = hecho.get(e["id"], {})
        if h.get("ok") or h.get("final"):
            continue
        when = datetime.fromisoformat(e["when"])
        if when > ahora:
            continue
        if e["kind"] == "trial" and e["net"] != "ig":  # solo Instagram tiene Trial: nunca sale como post normal
            hecho[e["id"]] = {"ok": 0, "final": 1, "error": "trial solo existe en Instagram", "ts": ahora.isoformat()}
            continue
        dep = e.get("despues_de")  # la historia que anuncia un post sale justo DESPUÉS de él, nunca antes ni sin él
        if dep:
            hd = hecho.get(dep, {})
            if not hd.get("ok"):
                if hd.get("final"):
                    hecho[e["id"]] = {"ok": 0, "final": 1, "error": "su post no salió", "ts": ahora.isoformat()}
                continue
        if ahora - when > timedelta(hours=MAX_RETRASO_H):
            hecho[e["id"]] = {"ok": 0, "final": 1, "error": "vencido sin publicar", "ts": ahora.isoformat()}
            continue
        rem = hecho_remoto() or {}  # justo antes de publicar: ¿otra corrida ya lo sacó?
        if rem.get(e["id"], {}).get("ok"):
            hecho[e["id"]] = rem[e["id"]]
            print("= ya publicado por otra corrida", e["id"])
            continue
        previo = ya_en_instagram(e)
        if previo:
            hecho[e["id"]] = {"ok": 1, "ext": previo, "ts": datetime.now(MX).isoformat(timespec="seconds"),
                              "nota": "ya estaba en Instagram (no se duplicó)"}
            guardar(hecho)
            print("= ya estaba en Instagram", e["id"], previo)
            continue
        try:
            ext = (ig if e["net"] == "ig" else fb)(e)
            hecho[e["id"]] = {"ok": 1, "ext": ext, "ts": datetime.now(MX).isoformat(timespec="seconds")}
            guardar(hecho)  # en cuanto sale, queda escrito en main
            print("✓", e["id"], e["net"], e["kind"], ext)
        except Exception as err:  # noqa: BLE001
            n = h.get("intentos", 0) + 1
            hecho[e["id"]] = {"ok": 0, "intentos": n, "final": int(n >= MAX_INTENTOS), "error": str(err)[:300],
                              "ts": ahora.isoformat()}
            print("✗", e["id"], e["net"], e["kind"], str(err)[:200])
    guardar(hecho)


if __name__ == "__main__":
    main()
