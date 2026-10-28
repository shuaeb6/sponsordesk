"""Baseline call-type classifier: TF-IDF features into logistic regression.

Trained on redacted text only, so the model never sees raw PII either.
"""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline, make_pipeline


@dataclass
class EvalReport:
    accuracy: float
    macro_f1: float
    labels: list[str]
    confusion: list[list[int]]
    train_size: int
    test_size: int
    test_ids: list[str]


@dataclass
class Prediction:
    label: str
    confidence: float
    ranked: list[tuple[str, float]]


class CallTypeClassifier:
    def __init__(self) -> None:
        self.pipeline: Pipeline = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
            LogisticRegression(max_iter=2000, C=4.0),
        )

    def fit_and_evaluate(
        self, ids: list[str], texts: list[str], labels: list[str], seed: int = 7
    ) -> EvalReport:
        x_train, x_test, y_train, y_test, _, id_test = train_test_split(
            texts, labels, ids, test_size=0.2, random_state=seed, stratify=labels
        )
        self.pipeline.fit(x_train, y_train)
        predicted = self.pipeline.predict(x_test)
        classes = sorted(set(labels))
        report = EvalReport(
            accuracy=float(accuracy_score(y_test, predicted)),
            macro_f1=float(f1_score(y_test, predicted, average="macro")),
            labels=classes,
            confusion=confusion_matrix(y_test, predicted, labels=classes).tolist(),
            train_size=len(x_train),
            test_size=len(x_test),
            test_ids=list(id_test),
        )
        return report

    def predict(self, text: str) -> Prediction:
        probs = self.pipeline.predict_proba([text])[0]
        classes = self.pipeline.classes_
        ranked = sorted(zip(classes, probs, strict=True), key=lambda kv: -kv[1])
        return Prediction(
            label=str(ranked[0][0]),
            confidence=float(ranked[0][1]),
            ranked=[(str(k), float(v)) for k, v in ranked[:3]],
        )
