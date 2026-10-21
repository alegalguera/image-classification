from photo_sorter.classifiers import Verdict
from photo_sorter.embedding_classifier import EmbeddingClassifier
from photo_sorter.references import EmbeddingCache
from photo_sorter.second_opinion import SecondOpinion

from conftest import RED, make_image


class LLM:
    def __init__(self, answer):
        self.answer, self.seen = answer, None

    def classify(self, image, mime, categories, is_video):
        self.seen = list(categories)
        return Verdict(self.answer, 0.9, "llm says so")


def test_second_opinion_only_when_unsure(fake_embedder, cfg, categories):
    primary = EmbeddingClassifier(fake_embedder, cfg, EmbeddingCache(None, "fake"))
    llm = LLM("Photos - Blue")
    clf = SecondOpinion(primary, llm, when_below=0.0)   # never triggers
    v = clf.classify(make_image(RED), "image/jpeg", categories, False)
    assert v.category == "Photos - Red" and llm.seen is None
    clf = SecondOpinion(primary, llm, when_below=1.1)   # always triggers
    v = clf.classify(make_image(RED), "image/jpeg", categories, False)
    assert v.category == "Photos - Blue" and v.confidence == 0.9 and "overrode" in v.reason
    assert llm.seen and len(llm.seen) <= 5 and "Photos - Red" in llm.seen
    llm2 = LLM("Photos - Red")
    v = SecondOpinion(primary, llm2, when_below=1.1).classify(make_image(RED), "image/jpeg", categories, False)
    assert v.category == "Photos - Red" and v.confidence >= 0.9 and "agrees" in v.reason
