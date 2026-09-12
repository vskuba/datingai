# Пачка фото персоны «diana» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or executing-plans. Шаги помечены `- [ ]`.

**Goal:** собрать первый пул медиа — 50 кадров персоны diana в `data/girls/diana/` (40 SFW + 10 интимных), генерация на ферме gx10-2.

**Architecture:** инструменты — общий слой `py_modules` (правило `docs/tool_rules.md` §1: универсальное в библиотеку): новый namespace `comfy_/comfy_gen.py` (батч через ComfyUI API по workflow+сценам) и `ai/ai_face.py` (эмбединг-косинус «то же лицо»). Проект знает только данные: `data/girls/diana/{workflow.json,scenes.json,_anchor.jpg,manifest.json}` и указатель submodule.

**Tech Stack:** ComfyUI API (prompt/history/view), httpx (sync), insightface+onnxruntime, exiftool, ffmpeg (сетка кадров).

**Spec:** `docs/superpowers/specs/2026-09-14-media-pipeline-diana-design.md`

## Global Constraints

- Ферма: `http://192.168.1.2:8188`; `8189` — чужой `reels_tts`, не трогать. адрес фермы аргумент `base`, в модуль не зашивается.
- Пул: `data/girls/<имя>/`; файлы `<32hex>.jpg` (sha256[:32]), EXIF срезан (`exiftool -all=`), строка манифеста `{"file","scene","seed","nsfw","face_on","score","pass"}`.
- Правила py_modules: публичные функции с префиксом namespace, приватные внизу, константы `UPPER_CASE` с префиксом, докстринг = контракт, CLI `python -m <ns>.<файл>`, кириллица в идентификаторах запрещена; коммит правки — в `py_modules`, в проекте — отдельным коммитом двигается указатель submodule.
- Порог QA стартует с 0.5; кадр не прошёл — один повтор с другим seed, снова нет — стоп, показать человеку.

---

### Task 1: ComfyUI gx10-2 виден с сети

**Files:** modify на ферме: контейнер `comfyui-gb10` (compose `~/comfyui-gb10/docker-compose.yml` уже содержит `8188:8188`).

**Interfaces:**
- Produces: `http://192.168.1.2:8188/system_stats` отдаёт JSON — вход для `comfy_gen_batch(base=...)` и экспорта `workflow.json`.

- [ ] **Step 1: сверить compose с тем, что запущено**

```bash
ssh gx10-2 'grep -A2 ports ~/comfyui-gb10/docker-compose.yml; docker port comfyui-gb10'
```
Ожидаю: compose даёт 8188 — продолжаю; compose НЕ даёт — стоп, вопрос человеку.

- [ ] **Step 2: пересоздать контейнер из текущего compose**

```bash
ssh gx10-2 'cd ~/comfyui-gb10 && docker compose up -d --force-recreate comfyui-gb10'
```

- [ ] **Step 3: проверить с этой машины**: `curl -s http://192.168.1.2:8188/system_stats | head -c 200` → JSON `devices`. Факт — одной строкой в ответ человеку.

### Task 2: Инструмент лица `ai/ai_face.py` (py_modules)

**Files:** create `py_modules/ai/ai_face.py`; modify `py_modules/requirements-vision.txt` (добавить `insightface>=1.0`, `onnxruntime>=1.20`).

**Interfaces:**
- Produces: `ai_face_score(path, anchor_path) -> float` — косинус эмбедингов (без лица на кадре `0.0`); CLI `python -m ai.ai_face check <dir> --anchor <файл> --min 0.5` — правит `score`/`pass` в `<dir>/manifest.json` построчно.

- [ ] **Step 1: написать `ai/ai_face.py`**

```python
"""Лицо кадра: эмбединг и сверка с якорем (spec media-pipeline 2026-09-14).

Косинус эмбединга кадра против якорного лица — метрика «кадр про эту же
девушку». Модель buffalo_l качается в root=~/.insightface сама.
"""
import json
import os
from pathlib import Path

import cv2
import insightface

AI_FACE_ROOT = os.path.expanduser("~/.insightface")
_app = None

def _face_app():
    global _app
    if _app is None:
        _app = insightface.FaceAnalysis(name="buffalo_l", root=AI_FACE_ROOT)
        _app.prepare(ctx_id=-1, det_size=(640, 640))
    return _app

def ai_face_score(path, anchor_path):
    """Косинус лица кадра против якоря; 0.0, если лицо не нашлось."""
    app = _face_app()
    faces = app.get(cv2.imread(str(path)))
    anchor = app.get(cv2.imread(str(anchor_path)))
    if not faces or not anchor:
        return 0.0
    return round(float(anchor[0].normed_embedding @ faces[0].normed_embedding), 3)

if __name__ == "__main__":
    import argparse
    ns = argparse.ArgumentParser(prog="ai_face")
    sub = ns.add_subparsers(dest="command", required=True)
    ch = sub.add_parser("check", help="проверить пачку против якоря, править manifest.json")
    ch.add_argument("dir"); ch.add_argument("--anchor", required=True); ch.add_argument("--min", type=float, default=0.5)
    ns = ns.parse_args()
    if ns.command == "check":
        d = Path(ns.dir); man = d / "manifest.json"
        rows = json.loads(man.read_text())
        for r in rows:
            r["score"] = ai_face_score(d / r["file"], ns.anchor)
            r["pass"] = r["score"] >= ns.min
            print(r["file"], r["score"], r["pass"])
        man.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
```

(лицо на кадре ищется то, что ближе к якорю, если их несколько — цикл `max(...)` по `faces` с той же операцией; это исполнитель уточнит правкой выше по факту кадров, константу порога не меняя.)

- [ ] **Step 2: deps**: `pip install insightface onnxruntime` (локально, CPU).
- [ ] **Step 3: проверить на якоре**: прогнать CLI по копии якоря как кадру → `pass: true`.
- [ ] **Step 4: коммит в `py_modules`**: `ai: эмбединг-косинус лица против якоря (QA пачки персоны)`.

### Task 3: Инструмент батча `comfy_/comfy_gen.py` (py_modules) + якорное лицо

**Files:** create `py_modules/comfy_/__init__.py`, `py_modules/comfy_/comfy_gen.py`; create `data/girls/diana/workflow.json` (экспорт из UI); после шага 3 — `data/girls/diana/_anchor.jpg`.

**Interfaces:**
- Consumes: `ai_face_score` не трогает; ходит в `base` по HTTP.
- Produces: `comfy_gen_batch(workflow, scene, out, *, base, persona, anchor=None, anchor_input=None, seed=1, n=1) -> list[dict]` — гоняет workflow n раз (seed..seed+n-1) с заполненными `__PROMPT__`/`__ANCHOR__`/`__SEED__`, качает кадры, режет EXIF, переименовывает в sha256[:32], дописывает `manifest.json`; CLI `python -m comfy_.comfy_gen run --workflow ... --scenes ... --scene ... --persona ... --anchor ... --base ... --out ... --seed ... --n ...`.

- [ ] **Step 1: написать `comfy_/comfy_gen.py`**

```python
"""Пачка кадров персоны через ComfyUI на ферме (spec media-pipeline 2026-09-14).

Workflow-экспорт (API format) с маркерами __PROMPT__ (текст CLIPTextEncode),
__ANCHOR__ (LoadImage пул-файла) и __SEED__ (KSampler): заполняются сценой.
Кадры приходят без EXIF, с hex-именем (sha256[:32]) и строкой в manifest.json.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

import httpx

COMFY_GEN_TIMEOUT = 600

def comfy_gen_batch(workflow, scene, out, *, base, persona, anchor=None,
                    anchor_input=None, seed=1, n=1):
    """scene: {"id","en","nsfw","face_on"} из scenes.json проекта."""
    wf = json.loads(Path(workflow).read_text())
    if anchor:
        if anchor_input:
            subprocess.run(["scp", str(anchor), f"gx10-2:{anchor_input}"], check=True)
        for node in wf.values():
            for k, v in list(node["inputs"].items()):
                if v == "__ANCHOR__":
                    node["inputs"][k] = Path(anchor).name
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rows_new = []
    with httpx.Client(timeout=COMFY_GEN_TIMEOUT) as c:
        for i in range(n):
            prompt = persona + scene["en"]
            filled = _fill(wf, prompt, seed + i)
            r = c.post(f"{base}/prompt", json={"prompt": filled})
            r.raise_for_status(); pid = r.json()["prompt_id"]
            while True:
                h = c.get(f"{base}/history/{pid}").json()
                st = h.get(pid, {}).get("status", {}).get("status_str")
                if st == "success": break
                if st == "error":
                    raise RuntimeError(f"ферма упала на сцене {scene['id']}")
                time.sleep(3)
            for o in h[pid].get("outputs", {}).values():
                for img in o.get("images", []):
                    b = c.get(f"{base}/view", params={"filename": img["filename"],
                         "subfolder": img["subfolder"], "type": "output"}).content
                    tmp = out / img["filename"]; tmp.write_bytes(b)
                    subprocess.run(["exiftool", "-all=", "-overwrite_original",
                                    str(tmp)], check=True, capture_output=True)
                    h32 = hashlib.sha256(b).hexdigest()[:32]
                    tmp.rename(out / f"{h32}.jpg")
                    row = {"file": f"{h32}.jpg", "scene": scene["id"], "seed": seed + i,
                           "nsfw": scene["nsfw"], "face_on": scene["face_on"],
                           "score": None, "pass": None}
                    _manifest_append(out, row); rows_new.append(row)
    return rows_new

def _fill(obj, prompt, seed):
    if isinstance(obj, dict):
        return {k: _fill(v, prompt, seed) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, prompt, seed) for v in obj]
    if obj == "__PROMPT__": return prompt
    if obj == "__SEED__": return seed
    return obj

def _manifest_append(out, row):
    man = out / "manifest.json"
    rows = json.loads(man.read_text()) if man.exists() else []
    rows.append(row)
    man.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")

if __name__ == "__main__":
    import argparse
    ns = argparse.ArgumentParser(prog="comfy_gen")
    ns.add_argument("command", choices=["run"])
    ns.add_argument("--workflow", required=True); ns.add_argument("--scenes")
    ns.add_argument("--scene", required=True); ns.add_argument("--persona", required=True)
    ns.add_argument("--anchor"); ns.add_argument("--anchor-input")
    ns.add_argument("--base", required=True); ns.add_argument("--out", required=True)
    ns.add_argument("--seed", type=int, default=1); ns.add_argument("--n", type=int, default=1)
    ns = ns.parse_args()
    scenes = {}
    if ns.scenes:
        scenes = {s["id"]: s for s in json.loads(Path(ns.scenes).read_text())}
    scene = scenes.get(ns.scene, {"id": ns.scene, "en": "",
                                 "nsfw": False, "face_on": True})
    for row in comfy_gen_batch(ns.workflow, scene, ns.out, base=ns.base,
                               persona=ns.persona, anchor=ns.anchor,
                               anchor_input=ns.anchor_input, seed=ns.seed, n=ns.n):
        print(row["file"], row["scene"], row["score"])
```

(для face-sheet сцена приходит в `--scenes` со своей `"en"`; `--anchor` на этом шаге не передаётся — без него `__ANCHOR__` в workflow не заполняется и PuLID-ветка в export-файле остаётся без якоря до шага 3.)

- [ ] **Step 2: face-sheet workflow**: собрать в UI (`http://192.168.1.2:8188`) чистый Flux-граф БЕЗ PuLID-ветки (лицо ещё не существует) и экспортировать API-format в `data/girls/diana/workflow.json`: UNETLoader `flux1-dev-fp8` + DualCLIPLoader(t5xxl, clip_l) → CLIPTextEncode `"__PROMPT__"` → EmptySD3LatentImage → sampler euler/FluxGuidance 1.0, seed `"__SEED__"` → VAESaveImage. Когда якорь выбран (шаг 3), в тот же файл добавляется PuLID-ветка: PulidFluxModelLoader `pulid_flux_v0.9.0` + PulidFluxInsightFaceLoader(CPU) + PulidFluxEvaClipLoader + ApplyPulidFlux* c LoadImage `"__ANCHOR__"` — и все последующие батчи идут уже через неё.
- [ ] **Step 3: лист лица и выбор**: 4 варианта прогона с разными seed → сетка `ffmpeg -y -pattern_type glob -i '<dir>/*.jpg' -vf scale=480:-1,tile=2x2 grid.png` → `show_image` человеку → выбранный кадр = `_anchor.jpg`.
- [ ] **Step 4: коммиты**: `py_modules` (`comfy_: батч персоны через ComfyUI API`), в проекте — указатель submodule.

### Task 4: Пачка из 50 кадров и итог

**Files:** create `data/girls/diana/scenes.json` (50 сцен ниже), `data/girls/diana/_anchor.jpg`; файлы кадров.

- [ ] **Step 1: `data/girls/diana/scenes.json` — ровно 50 сцен**

```json
[
{"id":"window_light_smile","en":"smiling softly by a bright window, beige cardigan, morning light","nsfw":false,"face_on":true},
{"id":"mirror_selfie","en":"mirror selfie in a cozy bedroom, white t-shirt and jeans, phone in hand","nsfw":false,"face_on":true},
{"id":"serious_portrait","en":"calm serious portrait, plain grey background, shoulder-length hair","nsfw":false,"face_on":true},
{"id":"golden_hour","en":"golden hour portrait, linen blouse, hair in a loose braid","nsfw":false,"face_on":true},
{"id":"cafe_laugh","en":"laughing at a cafe table with a cappuccino, warm tones","nsfw":false,"face_on":true},
{"id":"autumn_park","en":"walking in an autumn park, trench coat, falling leaves","nsfw":false,"face_on":true},
{"id":"book_bench","en":"reading a book on a park bench, glasses, cardigan","nsfw":false,"face_on":true},
{"id":"birthday_sparkler","en":"smiling with a sparkler at a birthday party, warm bokeh lights","nsfw":false,"face_on":true},
{"id":"street_full","en":"full-body shot on a cobblestone street, white sneakers, summer dress","nsfw":false,"face_on":true},
{"id":"park_full","en":"full body walking through a park, light jacket, tote bag","nsfw":false,"face_on":true},
{"id":"seaside_sundress","en":"full body at the sea promenade, sundress, sea in background","nsfw":false,"face_on":true},
{"id":"old_stairs","en":"full body on old town stairs, denim jacket","nsfw":false,"face_on":true},
{"id":"light_snow","en":"full body in light snow with a scarf, evening lights","nsfw":false,"face_on":true},
{"id":"flower_market","en":"choosing peonies at a flower market, tote on shoulder","nsfw":false,"face_on":true},
{"id":"kitchen_borscht","en":"cooking borscht in a home kitchen, apron, steam from the pot","nsfw":false,"face_on":true},
{"id":"varenyky","en":"making varenyky at a floured table, flour on hands","nsfw":false,"face_on":true},
{"id":"coffee_morning","en":"morning coffee at home kitchen, oversized shirt, messy bun","nsfw":false,"face_on":true},
{"id":"houseplants","en":"watering houseplants on a windowsill","nsfw":false,"face_on":true},
{"id":"livingroom_dance","en":"dancing barefoot in the living room, phone on the couch","nsfw":false,"face_on":true},
{"id":"ginger_cat","en":"sitting on the floor laughing with a ginger cat on her lap","nsfw":false,"face_on":true},
{"id":"yoga_balcony","en":"stretching on a yoga mat by the open balcony door, sportswear","nsfw":false,"face_on":true},
{"id":"canvas_easel","en":"painting on a canvas at a small easel, smock","nsfw":false,"face_on":true},
{"id":"pottery_wheel","en":"at a pottery wheel, clay on hands","nsfw":false,"face_on":true},
{"id":"violin","en":"practicing violin at a music stand","nsfw":false,"face_on":true},
{"id":"cafe_chess","en":"playing chess at a cafe table, focused look","nsfw":false,"face_on":true},
{"id":"kreshchatyk","en":"walking down Khreshchatyk boulevard in Kyiv, chestnut trees","nsfw":false,"face_on":true},
{"id":"saint_sofia","en":"near Saint Sophia Cathedral, light jacket","nsfw":false,"face_on":true},
{"id":"odesa_dawn","en":"Odesa seashore at dawn in a light shawl","nsfw":false,"face_on":true},
{"id":"lviv_fountain","en":"old Lviv market square by the fountain","nsfw":false,"face_on":true},
{"id":"funicular","en":"by the Kyiv funicular, city below in the back","nsfw":false,"face_on":true},
{"id":"yolka","en":"beside a Christmas tree with garland lights, knit sweater","nsfw":false,"face_on":true},
{"id":"evening_dress","en":"emerald evening dress with a cocktail glass, ballroom bokeh","nsfw":false,"face_on":true},
{"id":"candlelit_restaurant","en":"candlelit dinner at a restaurant, red lipstick","nsfw":false,"face_on":true},
{"id":"sunflowers","en":"hugging a big bouquet of sunflowers in a doorway","nsfw":false,"face_on":true},
{"id":"wedding_dress","en":"in a wedding dress holding a bouquet, shy smile","nsfw":false,"face_on":true},
{"id":"dnipro_jogging","en":"morning jogging along the Dnipro embankment","nsfw":false,"face_on":true},
{"id":"outdoor_rink","en":"on figure skates on an outdoor rink, scarf","nsfw":false,"face_on":true},
{"id":"beach_sunset","en":"stretching on the beach at sunset","nsfw":false,"face_on":true},
{"id":"postcard","en":"writing a travel postcard over a coffee","nsfw":false,"face_on":true},
{"id":"knitted_scarf","en":"trying on a knitted scarf, gift box on the table","nsfw":false,"face_on":true},
{"id":"chemise_window","en":"in a champagne silk chemise by the window at dawn","nsfw":true,"face_on":true},
{"id":"chemise_bed","en":"sitting on a made bed in a silk chemise, laughing, messy hair","nsfw":true,"face_on":true},
{"id":"bathrobe_coffee","en":"in a white bathrobe with coffee, bare legs on the sofa","nsfw":true,"face_on":true},
{"id":"lace_mirror","en":"in a black lace lingerie set at a mirror, warm lamp light","nsfw":true,"face_on":true},
{"id":"lace_reading","en":"reading on a sofa in nude lace lingerie","nsfw":true,"face_on":true},
{"id":"slip_silhouette","en":"backlit silhouette at a window in a slip dress","nsfw":true,"face_on":false},
{"id":"shirt_sunbeam","en":"morning silhouette in an oversized shirt, sunbeams through curtains","nsfw":true,"face_on":false},
{"id":"sea_surf_silhouette","en":"dawn silhouette at the sea shore, walking into the surf, face turned away","nsfw":true,"face_on":false},
{"id":"foam_bath","en":"bath with foam, shoulders and collarbones above the foam","nsfw":true,"face_on":false},
{"id":"wet_hair_towel","en":"wet hair wrapped in a towel, bare shoulders, bathroom steam","nsfw":true,"face_on":true}
]
```

- [ ] **Step 2: батч** — по сценам цикла: `python -m comfy_.comfy_gen run --workflow data/girls/diana/workflow.json --scenes data/girls/diana/scenes.json --scene <id> --persona "photo of diana, a 28-year-old Ukrainian woman with long dark brown hair and brown eyes, natural makeup, " --anchor data/girls/diana/_anchor.jpg --anchor-input '~/comfyui-gb10/data/input/' --base http://192.168.1.2:8188 --out data/girls/diana --seed 2000 --n 1` (seed сдвигается на номер сцены).
- [ ] **Step 3: QA**: `python -m ai.ai_face check data/girls/diana --anchor _anchor.jpg --min 0.5`; провалившиеся сцены — один повтор seed+1000; снова нет — стоп, показать человеку.
- [ ] **Step 4: итог**: сетка 50 кадров через ffmpeg-`tile` + `show_image`; числа человеку: прошло/брак.
- [ ] **Step 5: коммит проекта**: `data/girls/diana/` (50 кадров + манифест) и указатель submodule.
