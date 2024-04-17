.. _megatron_quantization:

Quantization
==========================

Post Training Quantization (PTQ)
--------------------------------

PTQ enables deploying a model in a low-precision format -- FP8, INT4 or INT8 -- for efficient serving. Different quantization methods are available including FP8 quantization, INT8 SmoothQuant and INT4 AWQ.

Model quantization has two primary benefits: reduced model memory requirements and increased inference throughput.

In NeMo, quantization is enabled by the Nvidia AMMO library -- a unified algorithmic model optimization & deployment toolkit.

The quantization process consists of the following steps:

1. Loading a model checkpoint using appropriate parallelism strategy for evaluation
2. Calibrating the model to obtain appropriate algorithm-specific scaling factors
3. Producing output directory or .qnemo tarball with model config (json), quantized weights (safetensors) and tokenizer config (yaml).

Loading models requires using AMMO spec defined in `megatron.core.deploy.gpt.model_specs module <https://github.com/NVIDIA/Megatron-LM/blob/main/megatron/core/deploy/gpt/model_specs.py>`_. Typically the calibration step is lightweight and uses a small dataset to obtain appropriate statistics for scaling tensors. The output directory produced (or a .qnemo tarball) is ready to be used to build a serving engine with the Nvidia TensorRT-LLM library. The engine build step is also soon to be the part of NeMo project and ``nemo.deploy`` and ``nemo.export`` modules, see https://github.com/NVIDIA/NeMo/pull/8690.

Quantization algorithm can also be conveniently set to ``"null"`` to perform only the weights export step using default precision for TensorRT-LLM deployment. This is useful to obtain baseline performance and accuracy results for comparison.


Example
^^^^^^^
The example below shows how to quantize the Llama2 70b model into FP8 precision, using tensor parallelism of 8 on a single DGX H100 node. The quantized model is intended for serving using 2 GPUs specified with ``export.inference_tensor_parallel`` parameter.

The script should be launched correctly with the number of processes equal to tensor parallelism. This is achieved with the ``mpirun`` command below.

.. code-block:: bash

    mpirun -n 8 python examples/nlp/language_modeling/megatron_llama_quantization.py \
        model_file=llama2-70b-base-bf16.nemo \
        tensor_model_parallel_size=8 \
        pipeline_model_parallel_size=1 \
        trainer.num_nodes=1 \
        trainer.devices=8 \
        trainer.precision=bf16 \
        quantization.algorithm=fp8 \
        export.decoder_type=llama \
        export.inference_tensor_parallel=2 \
        model_save=llama2-70b-base-fp8-qnemo



The output directory stores the following files:

.. code-block:: bash

    llama2-70b-base-fp8-qnemo/
    ├── config.json
    ├── rank0.safetensors
    ├── rank1.safetensors
    ├── tokenizer.model
    └── tokenizer_config.yaml


The TensorRT-LLM engine can be build with ``trtllm-build`` command, see `TensorRT-LLM documentation <https://github.com/NVIDIA/TensorRT-LLM/tree/main/examples/llama#fp8-post-training-quantization>`_.

.. code-block:: bash

    trtllm-build \
        --checkpoint_dir llama2-70b-base-fp8-qnemo \
        --output_dir engine_dir \
        --max_batch_size 8 \
        --max_input_len 2048 \
        --max_output_len 512



Known issues
^^^^^^^^^^^^
* Currently in NeMo quantizing and building TensorRT-LLM engines is limited to single-node use cases.
* Supported and tested model family is Llama2. Quantizing other model types is experimental and may not be fully supported.
* For INT8 SmoothQuant ``quantization.algorithm=int8_sq``, the TensorRT-LLM engine cannot be build with CLI ``trtllm-build`` command -- Python API and ``tensorrt_llm.builder`` should be used instead.


Please refer to the following papers for more details on quantization techniques.

References
----------

`FP8 Formats for Deep Learning, 2022 <https://arxiv.org/abs/2209.05433>`_

`SmoothQuant: Accurate and Efficient Post-Training Quantization for Large Language Models, 2022 <https://arxiv.org/abs/2211.10438>`_

`AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration, 2023 <https://arxiv.org/abs/2306.00978>`_
