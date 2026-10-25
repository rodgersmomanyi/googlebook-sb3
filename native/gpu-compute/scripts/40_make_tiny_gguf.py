#!/usr/bin/env python3
"""Synthetic random-weight tiny llama GGUF for the tests (no model downloads, INTERFACES 1.4 / 18.5).

usage: python3 -I -B 40_make_tiny_gguf.py <llama.cpp dir> <out.gguf> [q4_0|q8_0|f16] [seed]

Uses ONLY the pinned llama.cpp tree: its gguf-py package and its bundled vocabulary file
models/ggml-vocab-llama-spm.gguf (SentencePiece llama vocab, 32000 tokens). Architecture "llama": n_embd 64,
2 layers, 4 heads, n_ff 128, n_ctx_train 4096. Weights ~ N(0, 0.02) from numpy's seeded generator (deterministic).
The output rows of end-of-generation tokens are zero, so greedy decoding never stops on its own (long generations
for the queue/limit tests); with temperature > 0 they are just very unlikely.
"""
import os
import sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    llama, out = sys.argv[1], sys.argv[2]
    qname = sys.argv[3] if len(sys.argv) > 3 else "q4_0"
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1234
    sys.path.insert(0, os.path.join(llama, "gguf-py"))
    import numpy as np  # noqa: E402
    import gguf  # noqa: E402

    qtype = {"q4_0": gguf.GGMLQuantizationType.Q4_0, "q8_0": gguf.GGMLQuantizationType.Q8_0,
             "f16": gguf.GGMLQuantizationType.F16}[qname]
    ftype = {"q4_0": gguf.LlamaFileType.MOSTLY_Q4_0, "q8_0": gguf.LlamaFileType.MOSTLY_Q8_0,
             "f16": gguf.LlamaFileType.MOSTLY_F16}[qname]
    vocab = gguf.GGUFReader(os.path.join(llama, "models", "ggml-vocab-llama-spm.gguf"))
    tokens = vocab.fields["tokenizer.ggml.tokens"].contents()
    n_vocab = len(tokens)
    n_embd, n_layer, n_head, n_head_kv, n_ff = 64, 2, 4, 4, 128
    hd = n_embd // n_head

    w = gguf.GGUFWriter(out, "llama")
    w.add_name("gb-tiny-random-" + qname)
    w.add_context_length(4096)
    w.add_embedding_length(n_embd)
    w.add_block_count(n_layer)
    w.add_feed_forward_length(n_ff)
    w.add_head_count(n_head)
    w.add_head_count_kv(n_head_kv)
    w.add_rope_dimension_count(hd)
    w.add_layer_norm_rms_eps(1e-5)
    w.add_vocab_size(n_vocab)
    w.add_file_type(ftype)
    eog = set()
    for f in vocab.fields.values():
        if not f.name.startswith("tokenizer."):
            continue
        vt = f.types[0]
        sub = f.types[-1] if vt == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(f.name, f.contents(), vt, sub_type=sub)
        if f.name in ("tokenizer.ggml.eos_token_id", "tokenizer.ggml.eot_token_id", "tokenizer.ggml.eom_token_id"):
            eog.add(int(f.contents()))

    rng = np.random.default_rng(seed)

    def mat(rows, cols):
        return (rng.standard_normal((rows, cols)) * 0.02).astype(np.float32)

    def add_w(name, a):
        if qtype == gguf.GGMLQuantizationType.F16:
            w.add_tensor(name, a.astype(np.float16))
        else:
            w.add_tensor(name, gguf.quants.quantize(a, qtype), raw_dtype=qtype)

    add_w("token_embd.weight", mat(n_vocab, n_embd))
    w.add_tensor("output_norm.weight", np.ones(n_embd, dtype=np.float32))
    o = mat(n_vocab, n_embd)
    for t in eog:
        o[t, :] = 0.0
    add_w("output.weight", o)
    for i in range(n_layer):
        p = "blk.%d." % i
        w.add_tensor(p + "attn_norm.weight", np.ones(n_embd, dtype=np.float32))
        add_w(p + "attn_q.weight", mat(n_head * hd, n_embd))
        add_w(p + "attn_k.weight", mat(n_head_kv * hd, n_embd))
        add_w(p + "attn_v.weight", mat(n_head_kv * hd, n_embd))
        add_w(p + "attn_output.weight", mat(n_embd, n_head * hd))
        w.add_tensor(p + "ffn_norm.weight", np.ones(n_embd, dtype=np.float32))
        add_w(p + "ffn_gate.weight", mat(n_ff, n_embd))
        add_w(p + "ffn_up.weight", mat(n_ff, n_embd))
        add_w(p + "ffn_down.weight", mat(n_embd, n_ff))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    print("tiny gguf: %s %s vocab=%d eog=%s size=%d" % (out, qname, n_vocab, sorted(eog), os.path.getsize(out)))


if __name__ == "__main__":
    main()
