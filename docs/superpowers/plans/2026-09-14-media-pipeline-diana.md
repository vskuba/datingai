# Коллекция diana через swap-поток — план (переписан под заплатки + LoRA)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Шаги `- [x]` уже сделаны — это протокол фактов, по ним не идти дважды.

**Goal:** коллекция ≥50 фото персоны «diana» в `data/girls/diana/`: берём чужое готовое фото, выкраиваем область лица (`ai_face_crop`), генерим заплатку нашим лицом через **LoRA** (не PuLID — лицо даёт модель, натренированная на заплатках), вшиваем обратно (`ai_face_paste`). Область замены — параметр сцены: `face` / `face_hair` / `head_neck`.

**Architecture:** инструменты — общий слой `py_modules`: `comfy_/comfy_gen.py` (батч по workflow+сценам, гейт памяти по `_mem` workflow против `ram_free` фермы), `ai/ai_face.py` (score/crop/paste, `det_size` параметром). Проект знает только данные: `data/girls/diana/{workflows,patches,dataset,scenes*.json,_anchor.png}`.

**Tech Stack:** ComfyUI 0.29 API, flux1-dev-fp8 + TrainLoraNode, insightface antelopev2, httpx, exiftool, ffmpeg.

## Global Constraints

- Ферма gx10-2 `http://192.168.1.2:8188`, контейнер `comfyui-gb10`; GB10 — единая память 121.6 ГиБ, сосед-чат живёт на ней же и с неё не снимается; его память уже внутри `ram_free` — гейт обязан считать впритык по ФАКТУ замера, флаги контейнера (`expandable_segments:True`, `--highvram`) не трогать.
- Файлы `<32hex>.png` (sha256[:32]), EXIF срезан (`exiftool -all=`); строка манифеста `{"file","scene","seed","nsfw","face_on","score","pass"}`; у заплаток — `{"file","patch","mode","box"}`.
- Порог QA 0.5; провал — один повтор seed+1000; снова нет — стоп, показать человеку.
- Правки py_modules — отдельным коммитом там, в проекте — коммит указателя.

## Готово (факты)

- [x] Task 1: ферма видна из сети; compose/контейнер сверены (см. память сессии: флаги применяются аргументами main.py).
- [x] Task 2: `ai/ai_face.py` — score/crop/paste; `det_size` параметром (def0783): 8d5681d4 взят только на 320 (0.82 det, 0.681 против якоря).
- [x] Task 3: `comfy_/comfy_gen.py` — батч + гейт `_mem` против `ram_free` с запасом `COMFY_GEN_MEM_MARGIN=4.0`.
- [x] Заплатки: 15 штук в `patches/` + манифест; `dataset/` — исходники (17 кадров).
- [x] Замеры: обучение на патчах @512² → активных 75.5, free→0 (OOM на хвосте); offloading ничего не даёт (unified-память); проба 1@384² не упала, но free→0.0 и хост лёг. Веса ×2 (deepcopy TrainLoraNode) ≈ 61 ГиБ — ядро пика, параметрами сцены не снимается.

## Осталось

### Task 4: натренировать LoRA на 15 заплатках

- [ ] **Step 1: выбор размера обучающего кадра** — 384² (родное разрешение заплаток 133×173…337×463) vs 512²; пик мало зависит от числа файлов (randperm batch), решает весовое ядро.
- [ ] **Step 2: `_mem` train.json = факт. аппетит прогона на 15 заплатках целевым размером** (сейчас стоит 89 — пробный). Free в простое 93.7: впритык без запаса — прогон только когда сосед сжат; сомневаться — спросить человека про окно, не слать вслепую (проша 14@512 валила хост).
- [ ] **Step 3: прогон** `comfy_gen run --workflow workflows/train.json ...` → `output/diana/diana_face_*.safetensors` → забрать в проект (`models/loras/` или как скажет человек), seed 4001+.
- [ ] **Step 4:** пересчитать инференс-`_mem` (portrait/face_sheet/likeness/dataset стоят 25 — сверить с фактом при первом же прогоне).

### Task 5: инференс-workflow'ы под патч-поток

- [ ] **Step 1:** `portrait.json` ссылается на LoRA diana_face (LoRALoader), выход — заплатка под box, а не весь кадр.
- [ ] **Step 2:** face_sheet/likeness/dataset — сверить, что маркеры `__PROMPT__/__ANCHOR__/__SEED__` живы иSizes совпадают с box из манифеста заплаток.

### Task 6: коллекция 50 фото (swap)

- [ ] **Step 1: исходники** — чужие готовые фото под сцены; **источник 50 исходников — за человеком** (дата-фид или путь каталога), не выдумывать.
- [ ] **Step 2:** по исходнику: `ai_face_crop` → ген заплатки с LoRA (ракурсы/освещение из сцены) → `ai_face_paste` → `<32hex>.png` + строка манифеста.
- [ ] **Step 3: QA:** `ai_face check ... --min 0.5`; провал — seed+1000 один раз; снова нет — человеку.
- [ ] **Step 4:** сетка 50 кадров ffmpeg-tile + `show_image`; числа человеку: прошло/брак.
