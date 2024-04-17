# Copyright (c) 2024, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import shutil
from pathlib import Path
import torch

from tests.infer_data_path import get_infer_test_data

run_export_tests = True
try:
    from nemo.deploy import DeployPyTriton
    from nemo.deploy.nlp import NemoQueryLLM
    from nemo.export import TensorRTLLM
except Exception as e:
    run_export_tests = False


def get_accuracy_with_lambada(model, nq, task_ids, lora_uids, test_data_path=None):
    # lambada dataset based accuracy test, which includes more than 5000 sentences.
    # Use generated last token with original text's last token for accuracy comparison.
    # If the generated last token start with the original token, trtllm_correct make an increment.
    # It generates a CSV file for text comparison detail.

    if test_data_path is None:
        raise Exception("test_data_path cannot be None.")

    trtllm_correct = 0
    trtllm_deployed_correct = 0
    trtllm_correct_relaxed = 0
    trtllm_deployed_correct_relaxed = 0
    all_expected_outputs = []
    all_trtllm_outputs = []

    with open(test_data_path, 'r') as file:
        records = json.load(file)

        for record in records:
            prompt = record["text_before_last_word"]
            expected_output = record["last_word"].strip().lower()
            trtllm_output = model.forward(
                input_texts=[prompt],
                max_output_token=1,
                top_k=1,
                top_p=0,
                temperature=0.1,
                task_ids=task_ids,
                lora_uids=lora_uids,
            )
            trtllm_output = trtllm_output[0][0].strip().lower()

            all_expected_outputs.append(expected_output)
            all_trtllm_outputs.append(trtllm_output)

            if expected_output == trtllm_output:
                trtllm_correct += 1

            if (
                expected_output == trtllm_output
                or trtllm_output.startswith(expected_output)
                or expected_output.startswith(trtllm_output)
            ):
                if len(trtllm_output) == 1 and len(expected_output) > 1:
                    continue
                trtllm_correct_relaxed += 1

            if nq is not None:
                trtllm_deployed_output = nq.query_llm(
                    prompts=[prompt], max_output_token=1, top_k=1, top_p=0, temperature=0.1, task_id=task_ids,
                )
                trtllm_deployed_output = trtllm_deployed_output[0][0].strip().lower()

                if expected_output == trtllm_deployed_output:
                    trtllm_deployed_correct += 1

                if (
                    expected_output == trtllm_deployed_output
                    or trtllm_deployed_output.startswith(expected_output)
                    or expected_output.startswith(trtllm_deployed_output)
                ):
                    if len(trtllm_deployed_output) == 1 and len(expected_output) > 1:
                        continue
                    trtllm_deployed_correct_relaxed += 1

    trtllm_accuracy = trtllm_correct / len(all_expected_outputs)
    trtllm_accuracy_relaxed = trtllm_correct_relaxed / len(all_expected_outputs)

    trtllm_deployed_accuracy = trtllm_deployed_correct / len(all_expected_outputs)
    trtllm_deployed_accuracy_relaxed = trtllm_deployed_correct_relaxed / len(all_expected_outputs)

    return (
        trtllm_accuracy,
        trtllm_accuracy_relaxed,
        trtllm_deployed_accuracy,
        trtllm_deployed_accuracy_relaxed,
        all_trtllm_outputs,
        all_expected_outputs,
    )


def run_trt_llm_inference(
    model_name,
    model_type,
    prompt,
    checkpoint_path,
    trt_llm_model_dir,
    n_gpu=1,
    max_batch_size=8,
    max_input_token=128,
    max_output_token=128,
    ptuning=False,
    p_tuning_checkpoint=None,
    lora=False,
    lora_checkpoint=None,
    tp_size=None,
    pp_size=None,
    top_k=1,
    top_p=0.0,
    temperature=1.0,
    run_accuracy=False,
    debug=True,
    streaming=False,
    stop_words_list=None,
    test_deployment=False,
    test_data_path=None,
):
    if Path(checkpoint_path).exists():
        if n_gpu > torch.cuda.device_count():
            print(
                "Path: {0} and model: {1} with {2} gpus won't be tested since available # of gpus = {3}".format(
                    model_info["checkpoint"], model_name, n_gpu, torch.cuda.device_count()
                )
            )
            return None, None, None, None

        Path(trt_llm_model_dir).mkdir(parents=True, exist_ok=True)

        if debug:
            print("")
            print("")
            print(
                "################################################## NEW TEST ##################################################"
            )
            print("")

            print("Path: {0} and model: {1} with {2} gpus will be tested".format(checkpoint_path, model_name, n_gpu))

        prompt_embeddings_checkpoint_path = None
        task_ids = None
        max_prompt_embedding_table_size = 0

        if ptuning:
            if Path(p_tuning_checkpoint).exists():
                prompt_embeddings_checkpoint_path = p_tuning_checkpoint
                max_prompt_embedding_table_size = 8192
                task_ids = ["0"]
                if debug:
                    print("---- PTuning enabled.")
            else:
                print("---- PTuning could not be enabled and skipping the test.")
                return None, None, None, None

        lora_ckpt_list = None
        lora_uids = None
        use_lora_plugin = None
        lora_target_modules = None

        if lora:
            if Path(lora_checkpoint).exists():
                lora_ckpt_list = [lora_checkpoint]
                lora_uids = ["0", "-1", "0"]
                use_lora_plugin = "bfloat16"
                lora_target_modules = ["attn_qkv"]
                if debug:
                    print("---- LoRA enabled.")
            else:
                print("---- LoRA could not be enabled and skipping the test.")
                return None, None, None, None

        trt_llm_exporter = TensorRTLLM(trt_llm_model_dir, lora_ckpt_list)

        trt_llm_exporter.export(
            nemo_checkpoint_path=checkpoint_path,
            model_type=model_type,
            n_gpus=n_gpu,
            tensor_parallel_size=tp_size,
            pipeline_parallel_size=pp_size,
            max_input_token=max_input_token,
            max_output_token=max_output_token,
            max_batch_size=max_batch_size,
            max_prompt_embedding_table_size=max_prompt_embedding_table_size,
            use_lora_plugin=use_lora_plugin,
            lora_target_modules=lora_target_modules,
            save_nemo_model_config=True,
        )

        if ptuning:
            trt_llm_exporter.add_prompt_table(
                task_name="0", prompt_embeddings_checkpoint_path=prompt_embeddings_checkpoint_path,
            )

        output = trt_llm_exporter.forward(
            input_texts=prompt,
            max_output_token=max_output_token,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            task_ids=task_ids,
            lora_uids=lora_uids,
            streaming=streaming,
            stop_words_list=stop_words_list,
        )

        nq = None
        nm = None
        output_deployed = ""
        if test_deployment:
            nm = DeployPyTriton(model=trt_llm_exporter, triton_model_name=model_name, port=8000,)
            nm.deploy()
            nm.run()
            nq = NemoQueryLLM(url="localhost:8000", model_name=model_name)

            output_deployed = nq.query_llm(
                prompts=prompt,
                max_output_token=max_output_token,
                top_k=1,
                top_p=0.0,
                temperature=1.0,
                lora_uids=lora_uids,
            )

        if debug:
            print("")
            print("--- Prompt: ", prompt)
            print("")
            print("--- Output: ", output)
            print("")
            print("")
            print("--- Output deployed: ", output_deployed)
            print("")

        if run_accuracy:
            print("Start model accuracy testing ...")
            (
                trtllm_accuracy,
                trtllm_accuracy_relaxed,
                trtllm_deployed_accuracy,
                trtllm_deployed_accuracy_relaxed,
                all_trtllm_outputs,
                all_expected_outputs,
            ) = get_accuracy_with_lambada(trt_llm_exporter, nq, task_ids, lora_uids, test_data_path)
            if test_deployment:
                nm.stop()
            shutil.rmtree(trt_llm_model_dir)
            return trtllm_accuracy, trtllm_accuracy_relaxed, trtllm_deployed_accuracy, trtllm_deployed_accuracy_relaxed

        if test_deployment:
            nm.stop()
        shutil.rmtree(trt_llm_model_dir)
        return None, None, None, None
    else:
        raise Exception("Checkpoint {0} could not be found.".format(checkpoint_path))


def run_existing_checkpoints(
    model_name,
    n_gpus,
    tp_size=None,
    pp_size=None,
    ptuning=False,
    lora=False,
    streaming=False,
    run_accuracy=False,
    test_deployment=False,
    stop_words_list=None,
    test_data_path=None,
):
    if n_gpus > torch.cuda.device_count():
        print("Skipping the test due to not enough number of GPUs")
        return None, None, None, None

    test_data = get_infer_test_data()
    if not (model_name in test_data.keys()):
        raise Exception("Model {0} is not supported.".format(model_name))

    model_info = test_data[model_name]

    if n_gpus < model_info["min_gpus"]:
        print("Min n_gpus for this model is {0}".format(n_gpus))
        return None, None, None, None

    p_tuning_checkpoint = None
    if ptuning:
        if "p_tuning_checkpoint" in model_info.keys():
            p_tuning_checkpoint = model_info["p_tuning_checkpoint"]
        else:
            raise Exception("There is not ptuning checkpoint path defined.")

    lora_checkpoint = None
    if lora:
        if "lora_checkpoint" in model_info.keys():
            lora_checkpoint = model_info["lora_checkpoint"]
        else:
            raise Exception("There is not lora checkpoint path defined.")

    return run_trt_llm_inference(
        model_name=model_name,
        model_type=model_info["model_type"],
        prompt=model_info["prompt_template"],
        checkpoint_path=model_info["checkpoint"],
        trt_llm_model_dir=model_info["trt_llm_model_dir"],
        n_gpu=n_gpus,
        max_batch_size=model_info["max_batch_size"],
        max_input_token=512,
        max_output_token=model_info["max_output_token"],
        ptuning=ptuning,
        p_tuning_checkpoint=p_tuning_checkpoint,
        lora=lora,
        lora_checkpoint=lora_checkpoint,
        tp_size=tp_size,
        pp_size=pp_size,
        top_k=1,
        top_p=0.0,
        temperature=1.0,
        run_accuracy=run_accuracy,
        debug=True,
        streaming=streaming,
        stop_words_list=stop_words_list,
        test_deployment=test_deployment,
        test_data_path=test_data_path,
    )


def get_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=f"Deploy nemo models to Triton and benchmark the models",
    )

    parser.add_argument(
        "--model_name", type=str, required=True,
    )
    parser.add_argument(
        "--existing_test_models", default=False, action='store_true',
    )
    parser.add_argument(
        "--model_type", type=str, required=False,
    )
    parser.add_argument(
        "--min_gpus", type=int, default=1, required=True,
    )
    parser.add_argument(
        "--max_gpus", type=int,
    )
    parser.add_argument(
        "--checkpoint_dir", type=str, default="/tmp/nemo_checkpoint/", required=False,
    )
    parser.add_argument(
        "--trt_llm_model_dir", type=str,
    )
    parser.add_argument(
        "--max_batch_size", type=int, default=8,
    )
    parser.add_argument(
        "--max_input_token", type=int, default=256,
    )
    parser.add_argument(
        "--max_output_token", type=int, default=128,
    )
    parser.add_argument(
        "--p_tuning_checkpoint", type=str,
    )
    parser.add_argument(
        "--ptuning", default=False, action='store_true',
    )
    parser.add_argument(
        "--lora_checkpoint", type=str,
    )
    parser.add_argument(
        "--lora", default=False, action='store_true',
    )
    parser.add_argument(
        "--tp_size", type=int,
    )
    parser.add_argument(
        "--pp_size", type=int,
    )
    parser.add_argument(
        "--top_k", type=int, default=1,
    )
    parser.add_argument(
        "--top_p", type=float, default=0.0,
    )
    parser.add_argument(
        "--temperature", type=float, default=1.0,
    )
    parser.add_argument(
        "--run_accuracy", default=False, action='store_true',
    )
    parser.add_argument("--streaming", default=False, action="store_true")
    parser.add_argument(
        "--test_deployment", type=str, default="False",
    )
    parser.add_argument(
        "--debug", default=False, action='store_true',
    )
    parser.add_argument(
        "--ci_upload_test_results_to_cloud", default=False, action='store_true',
    )
    parser.add_argument(
        "--test_data_path", type=str, default=None,
    )

    return parser.parse_args()


def run_inference_tests(args):
    if args.test_deployment == "False":
        args.test_deployment = False
    else:
        args.test_deployment = True

    if args.run_accuracy:
        if args.test_data_path is None:
            raise Exception("test_data_path param cannot be None.")

    result_dic = {}

    if args.existing_test_models:
        n_gpus = args.min_gpus
        if args.max_gpus is None:
            args.max_gpus = args.min_gpus

        while n_gpus <= args.max_gpus:
            (
                trtllm_accuracy,
                trtllm_accuracy_relaxed,
                trtllm_deployed_accuracy,
                trtllm_deployed_accuracy_relaxed,
            ) = run_existing_checkpoints(
                model_name=args.model_name,
                n_gpus=n_gpus,
                ptuning=args.ptuning,
                lora=args.lora,
                tp_size=args.tp_size,
                pp_size=args.pp_size,
                streaming=args.streaming,
                test_deployment=args.test_deployment,
                run_accuracy=args.run_accuracy,
                test_data_path=args.test_data_path,
            )
            result_dic[n_gpus] = (
                trtllm_accuracy,
                trtllm_accuracy_relaxed,
                trtllm_deployed_accuracy,
                trtllm_deployed_accuracy_relaxed,
            )

            n_gpus = n_gpus * 2
    else:
        prompt_template = ["The capital of France is", "Largest animal in the sea is"]
        n_gpus = args.min_gpus
        if args.max_gpus is None:
            args.max_gpus = args.min_gpus

        while n_gpus <= args.max_gpus:
            (
                trtllm_accuracy,
                trtllm_accuracy_relaxed,
                trtllm_deployed_accuracy,
                trtllm_deployed_accuracy_relaxed,
            ) = run_trt_llm_inference(
                model_name=args.model_name,
                model_type=args.model_type,
                prompt=prompt_template,
                checkpoint_path=args.checkpoint_dir,
                trt_llm_model_dir=args.trt_llm_model_dir,
                n_gpu=n_gpus,
                max_batch_size=args.max_batch_size,
                max_input_token=args.max_input_token,
                max_output_token=args.max_output_token,
                ptuning=args.ptuning,
                p_tuning_checkpoint=args.p_tuning_checkpoint,
                lora=args.lora,
                lora_checkpoint=args.lora_checkpoint,
                tp_size=args.tp_size,
                pp_size=args.pp_size,
                top_k=args.top_k,
                top_p=args.top_p,
                temperature=args.temperature,
                run_accuracy=args.run_accuracy,
                debug=args.debug,
                streaming=args.streaming,
                test_deployment=args.test_deployment,
                test_data_path=args.test_data_path,
            )
            result_dic[n_gpus] = (
                trtllm_accuracy,
                trtllm_accuracy_relaxed,
                trtllm_deployed_accuracy,
                trtllm_deployed_accuracy_relaxed,
            )

            n_gpus = n_gpus * 2

    test_result = "PASS"
    print("======================================= Test Summary =======================================")
    for i, results in result_dic.items():
        if not results[0] is None and not results[1] is None:
            print(
                "Number of GPUS: {0}, Model Accuracy: {1}, Relaxed Model Accuracy: {2}, "
                "Deployed Model Accuracy: {3}, Deployed Relaxed Model Accuracy: {4}".format(
                    i, results[0], results[1], results[2], results[3]
                )
            )
            if results[1] < 0.5:
                test_result = "FAIL"

    print("=============================================================================================")
    print("TEST: " + test_result)
    if test_result == "FAIL":
        raise Exception("Model accuracy is below 0.5")


if __name__ == '__main__':
    args = get_args()
    run_inference_tests(args)
