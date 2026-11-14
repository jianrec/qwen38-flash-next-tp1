#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODEL=${MODEL:?set MODEL to the checkpoint directory}
IMAGE=${IMAGE:?set IMAGE to the SGLang container image}
CHECKPOINT_PATH=$(readlink -f "$MODEL")
CHECKPOINT_ID=$(basename "$CHECKPOINT_PATH")
VIEW=$ROOT/checkpoints/$CHECKPOINT_ID/model-reuse

[[ -z "${ALLOW_HOST:-}" || "$(hostname -s)" == "$ALLOW_HOST" ]] || { echo "host must be $ALLOW_HOST" >&2; exit 2; }
[[ ! -e "$VIEW" ]] || { echo "reuse view already exists: $VIEW" >&2; exit 2; }
mkdir -p "$VIEW"

for source in "$CHECKPOINT_PATH"/*; do
  [[ -f "$source" ]] || continue
  name=$(basename "$source")
  case "$name" in
    layer-*.safetensors|preserved-*.safetensors|ple-*.safetensors|*.json|*.txt|*.md|*.jinja|*.model|*.py)
      ln "$source" "$VIEW/$name"
      ;;
  esac
done

docker run --rm --name zj2-tp1-prepare-ple-scale \
  --entrypoint python3 \
  -v "$CHECKPOINT_PATH:/model:ro" \
  -v "$VIEW:/out" \
  "$IMAGE" -I -c '
from safetensors import safe_open
from safetensors.torch import save_file
name = "model.language_model.layers.1.ple.ple_embedding.ngram_embedding.weight_scale"
with safe_open("/model/ple-fp8-00000.safetensors", framework="pt") as source:
    scale = source.get_tensor(name)
save_file({"model.language_model.layers.1.ple.ple_embedding.ngram_embedding.weight_scale": scale}, "/out/preserved-ple-scale.safetensors")
'

python3 -I -c 'import json,os,pathlib,sys; root=pathlib.Path(sys.argv[1]); path=root/"model.safetensors.index.json"; data=json.load(open(path)); name="model.language_model.layers.1.ple.ple_embedding.ngram_embedding.weight_scale"; assert data["weight_map"][name]=="ple-fp8-00000.safetensors"; data["weight_map"][name]="preserved-ple-scale.safetensors"; tmp=root/"model.safetensors.index.json.tmp"; json.dump(data,open(tmp,"w"),ensure_ascii=False); os.replace(tmp,path)' "$VIEW"

python3 -I -c 'import json,pathlib,sys; root=pathlib.Path(sys.argv[1]); files=list(root.glob("[lp][ar]*.safetensors")); data=json.load(open(root/"model.safetensors.index.json")); indexed={root/name for name in data["weight_map"].values()}; selected=[p for p in files if p in indexed]; assert len(selected)==245,len(selected); assert len(list(root.glob("ple-fp8-*.safetensors")))==128; missing=[str(p) for p in indexed if not p.exists()]; assert not missing,missing; assert data["weight_map"]["model.language_model.layers.1.ple.ple_embedding.ngram_embedding.weight_scale"]=="preserved-ple-scale.safetensors"; print(f"validated {len(selected)} main-path and {len(list(root.glob(\"ple-fp8-*.safetensors\")))} PLE shards in {root}")' "$VIEW"
printf 'reuse_view=%s\n' "$VIEW"
