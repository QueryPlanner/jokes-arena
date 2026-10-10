import json
import contextlib
import io
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import generate_preview as generation


def completion(model, word):
    return {"model": model, "choices": [{"message": {"content": f"My {word} needs a vacation."}, "finish_reason": "stop"}], "usage": {"cost": 0.001}}


class GenerationTests(unittest.TestCase):
    def test_joke_validation(self):
        model = generation.MODELS[0]
        valid = completion(model, "coffee")
        valid["choices"][0]["message"]["content"] = "  Coffee, my only punctual colleague.  "
        self.assertEqual(generation.validate(valid, model, "coffee"), "Coffee, my only punctual colleague.")
        invalid = [None, {}, {"model": model}, {"model": model, "choices": []}]
        for text in [None, "", "  ", "I enjoy tea.", "coffeepot", "coffee " * 41]:
            item = completion(model, "coffee")
            item["choices"][0]["message"]["content"] = text
            invalid.append(item)
        item = completion(model, "coffee")
        item["choices"][0]["finish_reason"] = "length"
        invalid.append(item)
        invalid.append(completion("unexpected/model", "coffee"))
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ValueError):
                generation.validate(response, model, "coffee")

    def test_transport_sends_required_word_with_bounded_output(self):
        for model in generation.MODELS:
            with self.subTest(model=model), patch.object(generation, "urlopen") as transport:
                transport.return_value.__enter__.return_value.read.return_value = json.dumps(completion(model, "coffee")).encode()
                self.assertEqual(generation.request_completion("test-secret", model, "coffee")["model"], model)
                request = transport.call_args.args[0]
                data = json.loads(request.data)
                self.assertEqual(data["model"], model)
                self.assertIn('"coffee"', data["messages"][0]["content"])
                self.assertEqual(data["max_tokens"], 1500)
                self.assertEqual(transport.call_args.kwargs["timeout"], 60)
                self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")

    def test_transport_sanitizes_failures(self):
        for error in [HTTPError("url", 401, "secret upstream error", None, None), URLError("test-secret"), TimeoutError("test-secret"), OSError("test-secret")]:
            with self.subTest(error=type(error)), patch.object(generation, "urlopen", side_effect=error) as transport:
                with self.assertRaises(ValueError) as raised:
                    generation.request_completion("test-secret", generation.MODELS[0], "coffee")
                self.assertNotIn("test-secret", str(raised.exception))
                self.assertEqual(transport.call_count, 1)
        with patch.object(generation, "urlopen") as transport:
            transport.return_value.__enter__.return_value.read.return_value = b"not JSON"
            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                generation.request_completion("test-secret", generation.MODELS[0], "coffee")

    def test_batch_exports_only_complete_anonymous_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.jsonl"
            output = Path(directory) / "pairs.js"
            with patch.object(generation, "request_completion", side_effect=lambda key, model, word: completion(model, word)) as transport:
                pairs, calls = generation.generate("test-secret", list(generation.WORDS), receipt)
            self.assertEqual(calls, 6)
            self.assertEqual(transport.call_count, 6)
            self.assertEqual(len(pairs), 3)
            for pair in pairs:
                self.assertEqual(set(pair), {"word", "jokes"})
                self.assertEqual(len(pair["jokes"]), 2)
            self.assertEqual(len(receipt.read_text().splitlines()), 6)
            generation.export(pairs, output)
            public = output.read_text()
            for secret in ["test-secret", *generation.MODELS]:
                self.assertNotIn(secret, public)
            with self.assertRaises(FileExistsError):
                generation.export(pairs, output)

    def test_failed_second_model_keeps_receipt_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.jsonl"
            with patch.object(generation, "request_completion", side_effect=[completion(generation.MODELS[0], "coffee"), ValueError("OpenRouter HTTP 429")]) as transport:
                with self.assertRaisesRegex(ValueError, "429"):
                    generation.generate("test-secret", ["coffee"], receipt)
            self.assertEqual(transport.call_count, 2)
            records = [json.loads(line) for line in receipt.read_text().splitlines()]
            self.assertEqual([record["status"] for record in records], ["validated", "failed"])

    def test_invalid_batches_make_no_paid_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(generation, "request_completion") as transport:
            receipt = Path(directory) / "receipt.jsonl"
            for key, words in [("", ["coffee"]), ("key", []), ("key", ["coffee"] * 4), ("key", ["two words"]), ("key", ["Coffee"])]:
                with self.subTest(words=words), self.assertRaises(ValueError):
                    generation.generate(key, words, receipt)
            transport.assert_not_called()
            self.assertFalse(receipt.exists())

    def test_command_publishes_valid_batch_and_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pairs.js"
            receipt = Path(directory) / "receipt.jsonl"
            args = ["generate_preview", "--output", str(output), "--receipt", str(receipt), "--words", "coffee"]
            with patch("sys.argv", args), patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret"}), patch.object(generation, "request_completion", side_effect=lambda key, model, word: completion(model, word)), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(generation.main(), 0)
            self.assertIn("2 OpenRouter requests", stdout.getvalue())
            self.assertTrue(output.exists())
            self.assertEqual(len(receipt.read_text().splitlines()), 2)
            with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                generation.main()
            self.assertEqual(raised.exception.code, 2)

    def test_command_failure_returns_nonzero_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pairs.js"
            args = ["generate_preview", "--output", str(output), "--receipt", str(Path(directory) / "receipt.jsonl")]
            with patch("sys.argv", args), patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(generation.main(), 1)
            self.assertFalse(output.exists())
            self.assertIn("OPENROUTER_API_KEY is required", stderr.getvalue())

    def test_module_entrypoint_stops_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["generate_preview", "--output", str(Path(directory) / "pairs.js"), "--receipt", str(Path(directory) / "receipt.jsonl")]
            with patch("sys.argv", args), patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                runpy.run_module("scripts.generate_preview", run_name="__main__")
            self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
