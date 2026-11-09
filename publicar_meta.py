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
TOPE_REELS_DIA, SEPARACION_MIN = 4, 60  # Roberto: 4 reels al día, nunca pegados


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


FUERA = ("muert", "halloween", "navidad", "christmas", "en vivo", "live", "san judas", "remaster", "catrina", "llorona",
         "fantasma", "phantom", "calaca", "bruja", "zombie", "spooky", "ghost", "monster", "thriller", "almas", "witch",
         "vampir", "calavera", "ofrenda", "trick", "treat", "pumpkin", "calabaza", "terror", "horror", "scream", "boo", "goo goo muck", "watching me")
_TENDENCIA = None
_RECIENTES = None


BUSQUEDAS = {  # música CONGRUENTE con lo que se dice (Roberto 9-nov), no solo lo que está de moda
    "energia": ["workout motivation", "gym hip hop", "phonk", "energetic beat", "trap instrumental"],
    "tranquilo": ["lofi chill", "chill beat", "acoustic chill", "soft piano"],
    "reflexivo": ["cinematic inspiring", "motivational piano", "emotional piano", "ambient"],
}
BALADAS = ("rain", "lluvia", "sin ti", "confieso", "amor", "corazón", "llorar", "despecho", "ballad", "love")
_POOL: dict = {}


def musica(e):
    """Música OFICIAL de Instagram para TODO lo que la acepte (reels y posts), elegida por el ánimo del contenido:
    energía para entrenamiento, tranquila para nutrición/ciencia, reflexiva para frases. Determinista por publicación."""
    if e.get("audio") or e.get("sin_musica") or e["kind"] not in ("reel", "images"):
        return e.get("audio")
    animo = e.get("animo") or ("reflexivo" if e["kind"] == "images" else "energia")
    if animo not in _POOL:
        vistos, pool = set(), []
        for q in BUSQUEDAS.get(animo, BUSQUEDAS["energia"]):
            try:
                d = req(f"{G}/ig_audio?audio_type=music&user_id={e['target']}&search_query={urllib.parse.quote(q)}"
                        f"&access_token={TOK}")
            except RuntimeError:
                continue
            for a in d.get("audio", [])[:8]:
                t = (a.get("title") or "").lower()
                if a["audio_id"] in vistos or any(x in t for x in FUERA + BALADAS) or a.get("duration_in_ms", 0) < 20000:
                    continue
                vistos.add(a["audio_id"])
                pool.append(a)
        _POOL[animo] = pool
    pool = _POOL[animo]
    if not pool:
        return None
    import hashlib
    a = pool[int(hashlib.sha1(e["id"].encode()).hexdigest(), 16) % len(pool)]
    e["_musica"] = f"{a.get('title')} — {a.get('display_artist')} ({animo})"
    return {"audio_id": a["audio_id"], "audio_volume": 15 if e["kind"] == "reel" else 100, "video_volume": 100}


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
        audio = musica(e) if kind == "reel" else e.get("audio")
        if audio:  # música oficial de Instagram (tendencia) bajo la voz
            p["audio_configuration"] = json.dumps(audio)
            e["audio"] = audio
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
    else:
        if len(media) == 1:
            p = {"image_url": media[0], "caption": e.get("caption", ""), "access_token": TOK}
        else:
            kids = [req(base, {"image_url": u, "is_carousel_item": "true", "access_token": TOK})["id"] for u in media[:10]]
            p = {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": e.get("caption", ""), "access_token": TOK}
        audio = musica(e)
        if audio:
            p["audio_configuration"] = json.dumps(audio)
        try:
            c = req(base, p)
        except RuntimeError as err:
            if "audio_configuration" not in p:
                raise
            print("post: Instagram no aceptó música en imagen →", str(err)[:120])
            p.pop("audio_configuration")
            e["_musica"] = "sin música (Instagram no la aceptó en post)"
            c = req(base, p)
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
    """Segunda llave: si en los últimos 45 días ya hay en la cuenta un post con el mismo texto y del mismo tipo, no se
    vuelve a subir (se anota ese). Cubre cualquier pérdida de estado o un segundo camino de publicación."""
    if e["net"] != "ig" or e["kind"] == "story":
        return None
    global _RECIENTES
    if _RECIENTES is None:
        _RECIENTES, url = [], (f"{G}/{e['target']}/media?fields=id,caption,timestamp,media_product_type,"
                               f"is_shared_to_feed&limit=100&access_token={TOK}")
        try:
            for _ in range(3):
                d = req(url)
                _RECIENTES += d.get("data", [])
                url = (d.get("paging") or {}).get("next")
                if not url:
                    break
        except RuntimeError:
            pass
    d = {"data": _RECIENTES}
    cap = " ".join((e.get("caption") or "").split())[:120]
    if not cap:
        return None
    limite = datetime.now(timezone.utc) - timedelta(days=45)
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
        if e["net"] == "ig" and e["kind"] in ("reel", "trial"):  # última llave contra saturar el perfil
            if not 8 <= ahora.hour < 23:
                continue  # nunca de madrugada: espera a la mañana
            if e["kind"] == "reel":
                hoy_ = ahora.strftime("%Y-%m-%d")
                salidos = [hecho[x["id"]]["ts"][:19] for x in agenda if x["net"] == "ig" and x["kind"] == "reel"
                           and hecho.get(x["id"], {}).get("ok") and hecho[x["id"]].get("ts", "").startswith(hoy_)]
                if len(salidos) >= TOPE_REELS_DIA:
                    print("= tope diario de reels: espera", e["id"])
                    continue
                if salidos and ahora - datetime.fromisoformat(max(salidos)) < timedelta(minutes=SEPARACION_MIN):
                    print("= muy pegado al anterior: espera", e["id"])
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
            if _RECIENTES is not None and e["net"] == "ig":  # lo recién publicado también cuenta en esta corrida
                _RECIENTES.insert(0, {"id": ext, "caption": e.get("caption"), "media_product_type":
                                      "FEED" if e["kind"] == "images" else "REELS",
                                      "is_shared_to_feed": e["kind"] != "trial",
                                      "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")})
            if e.get("_musica"):
                hecho[e["id"]]["musica"] = e["_musica"]
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
