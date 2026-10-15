"""Phase 7 pre-study: text -> image search with EmbeddingGemma 2 (vision tower, MLX 8-bit) vs SigLIP 2.

Data: Flickr30k 1k test split (1000 photos, 5 English captions each), nlphuji/flickr_1k_test_image_text_retrieval.
Tasks:
  concepts  single-word queries, EN and PL ("dog" / "pies"); an image is relevant when one of its captions mentions
            the concept (captions miss things, so precision is a lower bound)
  captions  standard text->image retrieval with full captions (EN, all 5000) and 25 captions translated to Polish

Usage (each step in its own process, one model in memory at a time):
  uv run --group spike python spikes/phase7/image_eval.py embed-eg2
  uv run --group spike python spikes/phase7/image_eval.py embed-siglip
  uv run --group spike python spikes/phase7/image_eval.py evaluate
"""

import ast
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

HERE = Path(__file__).parent
DATA = HERE / "data"
IMAGES = DATA / "flickr" / "images_flickr_1k_test"
SIGLIP = ("google/siglip2-base-patch16-256", "3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab")

# (EN query, PL query, caption regex for relevance)
CONCEPTS = [
    ("dog", "pies", r"\b(dog|dogs|puppy|puppies)\b"),
    ("cat", "kot", r"\b(cat|cats|kitten)\b"),
    ("bicycle", "rower", r"\b(bicycle|bicycles|bike|bikes|biker|bikers|cyclist|cyclists|bmx)\b"),
    ("beach", "plaża", r"\b(beach)\b"),
    ("snow", "śnieg", r"\b(snow|snowy)\b"),
    ("guitar", "gitara", r"\b(guitar|guitars|guitarist)\b"),
    ("soccer", "piłka nożna", r"\b(soccer)\b"),
    ("horse", "koń", r"\b(horse|horses)\b"),
    ("boat", "łódź", r"\b(boat|boats|canoe|kayak)\b"),
    ("car", "samochód", r"\b(car|cars)\b"),
    ("food", "jedzenie", r"\b(food)\b"),
    ("child", "dziecko", r"\b(child|children|kid|kids|boy|girl)\b"),
]

# 25 captions (first caption of every 40th image) translated to Polish; filled from `captions` output.
PL_CAPTIONS: dict[str, str] = {
    "1007129816.jpg": "mężczyzna z kolczykami w uszach w okularach i pomarańczowej czapce",
    "1313961775.jpg": "mała dziewczynka unosi się na plecach w wodzie w kamizelce ratunkowej",
    "1664475761.jpg": "dwaj chłopcy przyczepiają owoce do roweru na polnej drodze",
    "21166859.jpg": "grupa muzyków gra na scenie przed publicznością",
    "2352452459.jpg": "mała dziewczynka w różowej bluzce śpi boso na kanapie",
    "2504764590.jpg": "osoba w zielonej kurtce rzuca patyk do wody czarno-białemu psu",
    "272045297.jpg": "chińczyk w restauracji czeka na gości",
    "2847514745.jpg": "grupa ludzi siedzi na trawie przy dużym budynku z czerwonej cegły",
    "2978735290.jpg": "chłopiec skacze nad innym chłopcem, obok leży piłka do nogi i stoi bramka",
    "314739483.jpg": "mężczyzna z czarną torbą idzie wzdłuż żółtej ściany z napisem E.S.E. Electronics",
    "3283368342.jpg": "czterech właścicieli stoi ze swoimi trzema nagrodzonymi psami",
    "3467073304.jpg": "siatkarka w bikini wyskakuje i ścina piłkę",
    "3593392955.jpg": "czarny pies biegnie z biało-szarym psem po ziemi",
    "3773310720.jpg": "mężczyzna w brązowej koszulce bez rękawów zszywa materiał na prowizorycznym zadaszeniu",
    "4039846249.jpg": "chłopiec w okularach przeciwsłonecznych i niebieskiej bluzie biegnie od namiotów",
    "430173345.jpg": "pies przygląda się fioletowemu przedmiotowi leżącemu w wysokiej trawie",
    "4510809964.jpg": "kobieta w kowbojskim kapeluszu idzie, a za nią mężczyzna w niebieskiej czapce i chłopiec",
    "464340358.jpg": "czterech robotników budowlanych stoi wokół dziury i zagląda do wiadra",
    "4752482394.jpg": "kobieta w słuchawkach pije kawę i pisze w notesie",
    "4814603619.jpg": "czterech uśmiechniętych mężczyzn pozuje do zdjęcia przy niebieskiej poręczy",
    "4923272678.jpg": "mężczyzna w czarnym kapeluszu i kurtce jedzie na rowerze",
    "5244864049.jpg": "mężczyzna w białej koszuli gra na białej gitarze przed mikrofonem",
    "5823310445.jpg": "dziewczynka w masce siedzi mężczyźnie na ramionach na zatłoczonej ulicy",
    "6699112713.jpg": "uśmiechnięta kobieta jeździ na nartach w białej czapce, w tle śnieg",
    "7446693604.jpg": "jeździec na byku przeskakuje przez płot, uciekając przed bykiem",
}


def rows() -> list[dict]:
    csv_path = next(Path.home().glob(".cache/huggingface/hub/datasets--nlphuji--flickr_1k_test_image_text_retrieval/snapshots/*/test_1k_flickr.csv"))
    out = []
    for r in csv.DictReader(open(csv_path)):
        out.append({"file": r["filename"], "captions": ast.literal_eval(r["raw"])})
    return out


def load_images(files):
    from PIL import Image

    for f in files:
        yield Image.open(IMAGES / f).convert("RGB")


def all_queries(data) -> list[str]:
    q = [c for c, _, _ in CONCEPTS] + [p for _, p, _ in CONCEPTS]
    q += [cap for r in data for cap in r["captions"]]
    q += list(PL_CAPTIONS.values())
    return q


def embed_eg2() -> None:
    import mlx.core as mx
    from huggingface_hub import snapshot_download
    from transformers import AutoProcessor

    from lantern.embedding.mlx_backend import REPO, REVISION
    from lantern.embedding.mlx_loader import load_embedding_gemma2

    data = rows()
    path = Path(snapshot_download(REPO, revision=REVISION, local_files_only=True))
    mx.set_memory_limit(4 * 1024**3)
    model = load_embedding_gemma2(path, lazy=True)
    model.audio_tower = model.embed_audio = None
    mx.eval(model.parameters())
    proc = AutoProcessor.from_pretrained(path)

    def run(inputs):
        e = model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
        mx.eval(e)
        return np.array(e.astype(mx.float32))

    t0 = time.perf_counter()
    img = np.concatenate([run(proc(text=proc.image_token, images=[im], return_tensors="np")) for im in load_images([r["file"] for r in data])])
    img_s = time.perf_counter() - t0
    mx.clear_cache()
    queries = all_queries(data)
    txt = []
    for i in range(0, len(queries), 32):
        batch = [f"task: search result | query: {q}" for q in queries[i : i + 32]]
        txt.append(run(proc.tokenizer(batch, padding=True, return_tensors="np")))
    np.savez(DATA / "eg2.npz", img=img, txt=np.concatenate(txt), queries=np.array(queries))
    print(f"eg2: {len(data)} images in {img_s:.0f}s ({len(data) / img_s:.1f} img/s), peak {mx.get_peak_memory() / 2**30:.2f} GB")


def check_eg2_parity() -> None:
    """MLX 8-bit image embeddings vs sentence-transformers fp32 (original weights) on 3 photos."""
    import torch
    from sentence_transformers import SentenceTransformer

    data = rows()[:3]
    st = SentenceTransformer("google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b", device="mps",
                             config_kwargs={"audio_config": None}, model_kwargs={"torch_dtype": torch.float32})  # fmt: skip
    ref = st.encode([{"image": im} for im in load_images([r["file"] for r in data])], normalize_embeddings=True)
    mine = np.load(DATA / "eg2.npz")["img"][:3]
    print("eg2 MLX vs ST fp32 image cos:", np.round((ref * mine).sum(1), 4))


def embed_siglip() -> None:
    import torch
    from transformers import AutoModel, AutoProcessor

    data = rows()
    name, rev = SIGLIP
    model = AutoModel.from_pretrained(name, revision=rev, torch_dtype=torch.float32).to("mps").eval()
    proc = AutoProcessor.from_pretrained(name, revision=rev)
    def feats(out):  # transformers 5 returns a model output; older versions a tensor
        return torch.nn.functional.normalize(out if torch.is_tensor(out) else out.pooler_output, dim=-1).cpu().numpy()

    t0 = time.perf_counter()
    img = []
    with torch.no_grad():
        ims = list(load_images([r["file"] for r in data]))
        for i in range(0, len(ims), 16):
            inp = proc(images=ims[i : i + 16], return_tensors="pt").to("mps")
            img.append(feats(model.get_image_features(**inp)))
        img_s = time.perf_counter() - t0
        queries = all_queries(data)
        txt = []
        for i in range(0, len(queries), 64):
            # SigLIP was trained with fixed-length padding (64 tokens) and lowercase text.
            inp = proc(text=[q.lower() for q in queries[i : i + 64]], padding="max_length", max_length=64,
                       truncation=True, return_tensors="pt").to("mps")  # fmt: skip
            txt.append(feats(model.get_text_features(**inp)))
    np.savez(DATA / "siglip.npz", img=np.concatenate(img), txt=np.concatenate(txt), queries=np.array(queries))
    print(f"siglip2: {len(data)} images in {img_s:.0f}s ({len(data) / img_s:.1f} img/s)")


def evaluate() -> None:
    data = rows()
    out = {}
    for name in ("eg2", "siglip"):
        f = DATA / f"{name}.npz"
        if not f.exists():
            continue
        z = np.load(f)
        img, txt, queries = z["img"], z["txt"], list(z["queries"])
        qv = {q: txt[i] for i, q in enumerate(queries)}
        res = {}
        for lang, idx in (("en", 0), ("pl", 1)):
            p10, ap = [], []
            for concept in CONCEPTS:
                rel = np.array([bool(re.search(concept[2], " ".join(r["captions"]).lower())) for r in data])
                order = np.argsort(-(img @ qv[concept[idx]]))
                hits = rel[order]
                p10.append(hits[:10].mean())
                prec = np.cumsum(hits) / np.arange(1, len(hits) + 1)
                ap.append((prec * hits).sum() / max(1, hits.sum()))
            res[f"concept_p@10_{lang}"] = round(float(np.mean(p10)), 3)
            res[f"concept_mAP_{lang}"] = round(float(np.mean(ap)), 3)
        dog_en = np.argsort(-(img @ qv["dog"]))[:10]
        dog_pl = np.argsort(-(img @ qv["pies"]))[:10]
        dog_rel = [bool(re.search(CONCEPTS[0][2], " ".join(data[i]["captions"]).lower())) for i in range(len(data))]
        res["dog_top10_en"] = int(sum(dog_rel[i] for i in dog_en))
        res["dog_top10_pl"] = int(sum(dog_rel[i] for i in dog_pl))
        # Caption retrieval (EN): 5000 captions, each must find its own image.
        ranks = []
        for i, r in enumerate(data):
            for cap in r["captions"]:
                s = img @ qv[cap]
                ranks.append(int((s > s[i]).sum()) + 1)
        ranks = np.array(ranks)
        res["caption_R@1_en"] = round(float((ranks <= 1).mean()), 3)
        res["caption_R@5_en"] = round(float((ranks <= 5).mean()), 3)
        if PL_CAPTIONS:
            pr = []
            for file, cap in PL_CAPTIONS.items():
                i = next(k for k, r in enumerate(data) if r["file"] == file)
                s = img @ qv[cap]
                pr.append(int((s > s[i]).sum()) + 1)
            pr = np.array(pr)
            res["caption_R@1_pl"] = round(float((pr <= 1).mean()), 3)
            res["caption_R@5_pl"] = round(float((pr <= 5).mean()), 3)
        out[name] = res
    print(json.dumps(out, indent=1))
    (DATA / "results.json").write_text(json.dumps(out, indent=1))


def print_captions() -> None:
    for r in rows()[::40]:
        print(f'    "{r["file"]}": "",  # {r["captions"][0]}')


if __name__ == "__main__":
    {"embed-eg2": embed_eg2, "parity": check_eg2_parity, "embed-siglip": embed_siglip, "evaluate": evaluate,
     "captions": print_captions}[sys.argv[1]]()  # fmt: skip
