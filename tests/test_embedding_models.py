"""
So sánh chất lượng embedding của 3 model cho domain toán tiếng Việt.
Chạy: python tests/test_embedding_models.py

Cách đo: với mỗi cặp (query, chunk_liên_quan), tính cosine similarity.
Model nào cho similarity cao hơn = phân biệt ngữ nghĩa tốt hơn.
"""
import numpy as np
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os
from google import genai

load_dotenv()

# --- Test data: cặp (query, relevant_chunk, irrelevant_chunk) ---
TEST_CASES = [
    {
        "query": "tính đạo hàm của hàm số y = x^3 + 2x",
        "relevant": "Đạo hàm của hàm lũy thừa y = x^n là y' = nx^(n-1). Ví dụ: y = x^3 thì y' = 3x^2.",
        "irrelevant": "Xác suất của biến cố A là tỉ số giữa số kết quả thuận lợi và tổng số kết quả.",
    },
    {
        "query": "giải phương trình bậc hai ax^2 + bx + c = 0",
        "relevant": "Phương trình bậc hai ax^2 + bx + c = 0 có delta = b^2 - 4ac. Nếu delta > 0 có 2 nghiệm phân biệt.",
        "irrelevant": "Hình hộp chữ nhật có 6 mặt, 12 cạnh và 8 đỉnh.",
    },
    {
        "query": "tính diện tích hình tròn bán kính r",
        "relevant": "Diện tích hình tròn bán kính r là S = πr². Chu vi hình tròn là C = 2πr.",
        "irrelevant": "Logarithm cơ số a của b ký hiệu là log_a(b), bằng số mũ x sao cho a^x = b.",
    },
    {
        "query": "logarithm tự nhiên và số e",
        "relevant": "Logarithm tự nhiên ln(x) = log_e(x) với e ≈ 2.718. Đạo hàm của ln(x) là 1/x.",
        "irrelevant": "Tam giác đều có 3 cạnh bằng nhau và 3 góc bằng 60 độ.",
    },
    {
        "query": "tính giới hạn của dãy số khi n tiến tới vô cực",
        "relevant": "Giới hạn của dãy số a_n khi n→∞: nếu |q| < 1 thì q^n → 0. Dãy hội tụ khi có giới hạn hữu hạn.",
        "irrelevant": "Số phức z = a + bi có phần thực a và phần ảo b.",
    },
]


def cosine_similarity(a, b):
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def evaluate_model(name, embed_fn):
    print(f"\n{'='*60}")
    print(f"Model: {name}")
    print(f"{'='*60}")
    scores_rel, scores_irrel, gaps = [], [], []

    for i, tc in enumerate(TEST_CASES):
        q = embed_fn(tc["query"])
        r = embed_fn(tc["relevant"])
        ir = embed_fn(tc["irrelevant"])

        sim_rel = cosine_similarity(q, r)
        sim_irrel = cosine_similarity(q, ir)
        gap = sim_rel - sim_irrel

        scores_rel.append(sim_rel)
        scores_irrel.append(sim_irrel)
        gaps.append(gap)

        print(f"  [{i+1}] relevant={sim_rel:.3f}  irrelevant={sim_irrel:.3f}  gap={gap:+.3f}")

    print(f"  --- avg_relevant={np.mean(scores_rel):.3f} | avg_irrelevant={np.mean(scores_irrel):.3f} | avg_gap={np.mean(gaps):+.3f}")
    return {"gap": np.mean(gaps), "relevant": np.mean(scores_rel), "irrelevant": np.mean(scores_irrel)}


def main():
    results = {}

    # Model 1: BAAI/bge-m3
    print("\nLoading BAAI/bge-m3...")
    bge = SentenceTransformer("BAAI/bge-m3")
    results["BAAI/bge-m3"] = evaluate_model(
        "BAAI/bge-m3 (1024-dim)",
        lambda text: bge.encode(text).tolist()
    )

    # Model 2: paraphrase-multilingual-MiniLM-L12-v2
    print("\nLoading paraphrase-multilingual-MiniLM-L12-v2...")
    minilm = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    results["MiniLM-L12"] = evaluate_model(
        "paraphrase-multilingual-MiniLM-L12-v2 (384-dim)",
        lambda text: minilm.encode(text).tolist()
    )

    # Model 3: gemini-embedding-2
    gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    def gemini_embed(text):
        r = gemini.models.embed_content(model="gemini-embedding-2", contents=text)
        return r.embeddings[0].values

    results["gemini-embedding-2"] = evaluate_model(
        "gemini-embedding-2 (3072-dim)",
        gemini_embed
    )

    # Tổng kết
    print(f"\n{'='*60}")
    print("KẾT QUẢ (avg gap = relevant - irrelevant, cao hơn = tốt hơn)")
    print(f"{'='*60}")
    print(f"  {'Model':<45} {'gap':>6} {'relevant':>10} {'irrelevant':>12}")
    print(f"  {'-'*45} {'-'*6} {'-'*10} {'-'*12}")
    for model, r in sorted(results.items(), key=lambda x: -x[1]["gap"]):
        print(f"  {model:<45} {r['gap']:>+6.3f} {r['relevant']:>10.3f} {r['irrelevant']:>12.3f}")


if __name__ == "__main__":
    main()
