"""Trainable small neural language classifier and evaluation helpers."""
from collections import Counter, defaultdict
import csv
import json
import math
import random
from pathlib import Path
from typing import Any

from .text import char_ngrams, clean_text, script_language
from ..settings import settings, ROOT

DATASET_PATH = ROOT / "data" / "language_samples.csv"
LABELS = ["en", "es", "fr", "hi"]
DATASET_VERSION = "synthetic-language-phrases-v1"
SEED = 301


def read_dataset(path: Path = DATASET_PATH) -> list[dict[str, str]]:
    rows = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = clean_text(row.get("text", ""), 4000)
            label = (row.get("language") or "").strip()
            if not text or label not in LABELS:
                raise ValueError("Dataset rows need nonempty text and a supported language label.")
            key = text.casefold()
            if key in seen:
                raise ValueError(f"Duplicate sample in language dataset: {text[:60]}")
            seen.add(key)
            rows.append({"text": text, "label": label})
    if not rows or any(not any(row["label"] == label for row in rows) for label in LABELS):
        raise ValueError("Dataset must include examples for each supported language.")
    return rows


def stratified_split(rows: list[dict[str, str]], seed: int = SEED) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    train, validation, test = [], [], []
    for label in LABELS:
        samples = [row for row in rows if row["label"] == label]
        if len(samples) < 5:
            raise ValueError(f"Need at least five examples for {label} to create train/validation/test splits.")
        rng.shuffle(samples)
        n_test = max(1, round(len(samples) * .2))
        n_val = max(1, round(len(samples) * .2))
        test.extend(samples[:n_test]); validation.extend(samples[n_test:n_test+n_val]); train.extend(samples[n_test+n_val:])
    rng.shuffle(train); rng.shuffle(validation); rng.shuffle(test)
    return train, validation, test


def fit_vocab(rows: list[dict], max_features: int = 240) -> list[str]:
    counts = Counter(gram for row in rows for gram in char_ngrams(row["text"], 3))
    return [gram for gram, _ in counts.most_common(max_features)]


def vector(text: str, vocab: list[str]) -> list[float]:
    grams = set(char_ngrams(text, 3))
    return [1.0 if gram in grams else 0.0 for gram in vocab]


def _softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(max(-30, min(30, value-peak))) for value in logits]
    total = sum(exps)
    return [value/total for value in exps]


def _new_network(input_size: int, hidden: int, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    scale = math.sqrt(2 / max(1,input_size))
    return {"hidden": hidden,
            "w1": [[rng.uniform(-scale,scale) for _ in range(input_size)] for _ in range(hidden)],
            "b1": [0.0]*hidden,
            "w2": [[rng.uniform(-.15,.15) for _ in range(hidden)] for _ in LABELS],
            "b2": [0.0]*len(LABELS)}


def network_probabilities(features: list[float], net: dict) -> list[float]:
    hidden = [max(0.0, sum(w*x for w,x in zip(row,features))+bias) for row,bias in zip(net["w1"],net["b1"])]
    return _softmax([sum(w*h for w,h in zip(row,hidden))+bias for row,bias in zip(net["w2"],net["b2"])])


def fit_network(train: list[dict], vocab: list[str], epochs: int = 70, seed: int = SEED) -> dict:
    net = _new_network(len(vocab), 14, seed)
    rng = random.Random(seed)
    samples = [(vector(row["text"],vocab), LABELS.index(row["label"])) for row in train]
    for epoch in range(epochs):
        rng.shuffle(samples)
        rate = .055 * (1 - .65 * epoch / max(1,epochs))
        for features, target in samples:
            hpre = [sum(w*x for w,x in zip(row,features))+b for row,b in zip(net["w1"],net["b1"])]
            hidden = [max(0.0,x) for x in hpre]
            probs = _softmax([sum(w*h for w,h in zip(row,hidden))+b for row,b in zip(net["w2"],net["b2"])])
            dlogits = probs[:]; dlogits[target] -= 1
            dhidden = [sum(net["w2"][o][j]*dlogits[o] for o in range(len(LABELS))) * (1.0 if hpre[j] > 0 else 0.0) for j in range(net["hidden"])]
            for out in range(len(LABELS)):
                for j in range(net["hidden"]): net["w2"][out][j] -= rate*dlogits[out]*hidden[j]
                net["b2"][out] -= rate*dlogits[out]
            for j in range(net["hidden"]):
                for i in range(len(vocab)): net["w1"][j][i] -= rate*dhidden[j]*features[i]
                net["b1"][j] -= rate*dhidden[j]
    return net


def fit_naive_bayes(train: list[dict], vocab: list[str]) -> dict:
    token_counts = {label:Counter() for label in LABELS}; totals = Counter(); docs = Counter()
    vocab_set = set(vocab)
    for row in train:
        label = row["label"]; docs[label] += 1
        for gram in set(char_ngrams(row["text"],3)) & vocab_set:
            token_counts[label][gram] += 1; totals[label] += 1
    return {"token_counts": {label:dict(c) for label,c in token_counts.items()}, "totals":dict(totals), "docs":dict(docs)}


def bayes_probabilities(text: str, vocab: list[str], model: dict) -> list[float]:
    active = set(char_ngrams(text,3)) & set(vocab); docs_total = sum(model["docs"].values()); logits=[]
    for label in LABELS:
        prior = math.log((model["docs"].get(label,0)+1)/(docs_total+len(LABELS)))
        denom = model["totals"].get(label,0)+len(vocab)
        logits.append(prior+sum(math.log((model["token_counts"][label].get(g,0)+1)/denom) for g in active))
    return _softmax(logits)


def _metrics(rows: list[dict], predict) -> dict:
    matrix = {actual:{predicted:0 for predicted in LABELS} for actual in LABELS}
    for row in rows:
        predicted = predict(row["text"])
        matrix[row["label"]][predicted] += 1
    total = len(rows); correct = sum(matrix[label][label] for label in LABELS)
    per_class={}
    for label in LABELS:
        tp=matrix[label][label]; fp=sum(matrix[a][label] for a in LABELS if a!=label); fn=sum(matrix[label][p] for p in LABELS if p!=label)
        precision=tp/(tp+fp) if tp+fp else 0; recall=tp/(tp+fn) if tp+fn else 0
        per_class[label]={"precision":round(precision,3),"recall":round(recall,3),"f1":round(2*precision*recall/(precision+recall),3) if precision+recall else 0}
    return {"accuracy":round(correct/total,3) if total else 0,"macro_f1":round(sum(x["f1"] for x in per_class.values())/len(LABELS),3),"examples":total,"labels":LABELS,"confusion_matrix":matrix,"per_class":per_class}


def train_and_save(output: Path | None = None) -> dict:
    rows = read_dataset(); train, validation, test = stratified_split(rows)
    vocab = fit_vocab(train)
    nn = fit_network(train,vocab)
    nb = fit_naive_bayes(train,vocab)
    nn_predict=lambda text: LABELS[max(range(len(LABELS)),key=lambda i:network_probabilities(vector(text,vocab),nn)[i])]
    nb_predict=lambda text: LABELS[max(range(len(LABELS)),key=lambda i:bayes_probabilities(text,vocab,nb)[i])]
    metrics={"neural_network_validation":_metrics(validation,nn_predict),"neural_network_test":_metrics(test,nn_predict),"naive_bayes_validation":_metrics(validation,nb_predict),"naive_bayes_test":_metrics(test,nb_predict)}
    artifact={"version":"language-classifier-v1","dataset_version":DATASET_VERSION,"preprocessing_version":"unicode-nfkc-character-trigram-v1","feature_config":{"type":"binary_character_3gram","max_features":240},"training_parameters":{"model":"one-hidden-layer softmax neural classifier","hidden_units":14,"epochs":70,"seed":SEED},"seed":SEED,"labels":LABELS,"vocabulary":vocab,"network":nn,"metrics":metrics,"split_sizes":{"train":len(train),"validation":len(validation),"test":len(test)},"dataset_note":"Synthetic development phrases; metrics are illustrative and not evidence of real-world language accuracy."}
    target=output or settings.model_path
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(artifact,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    return artifact


def load_model(path: Path | None = None) -> dict | None:
    target=path or settings.model_path
    try:
        model=json.loads(target.read_text(encoding="utf-8"))
        vocab=model.get("vocabulary"); network=model.get("network")
        if model.get("labels") != LABELS or not isinstance(vocab,list) or not vocab or not isinstance(network,dict):
            return None
        if len(network.get("w1",[]))!=network.get("hidden") or any(len(row)!=len(vocab) for row in network.get("w1",[])): return None
        if len(network.get("w2",[]))!=len(LABELS) or any(len(row)!=network.get("hidden") for row in network.get("w2",[])): return None
        if len(network.get("b1",[]))!=network.get("hidden") or len(network.get("b2",[]))!=len(LABELS): return None
        return model
    except (OSError,ValueError,TypeError):
        return None


def predict_language(text: str, model: dict | None) -> dict:
    text=clean_text(text,4000)
    if not text:
        return {"language":"unknown","confidence":0.0,"source":"empty"}
    script=script_language(text)
    if script:
        return {"language":script,"confidence":0.99,"source":"script"}
    if not model:
        return {"language":"unknown","confidence":0.0,"source":"unavailable"}
    scores=network_probabilities(vector(text,model["vocabulary"]),model["network"])
    index=max(range(len(scores)),key=lambda i:scores[i])
    return {"language":LABELS[index],"confidence":round(scores[index],3),"source":"trained_model"}


def model_summary(model: dict | None) -> dict:
    if not model:
        return {"ready":False,"version":None,"metrics":None,"note":"Train the included synthetic development dataset to enable the local language classifier."}
    return {"ready":True,"version":model["version"],"metrics":model["metrics"],"split_sizes":model["split_sizes"],"note":model["dataset_note"]}
