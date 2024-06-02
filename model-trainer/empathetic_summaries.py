# importing all the necessary models
import os
import torch
import nltk
from tqdm import tqdm
import pandas as pd
import matplotlib.pyplot as plt
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    TrainingArguments,
    Trainer,
    pipeline,
    set_seed
)
from datasets import load_dataset, load_metric
import logging
from utils import calculate_metric_on_test_ds

# Setup logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler('trainer.log'),
                        logging.StreamHandler()
                    ])

nltk.download("punkt")

# Check GPU availability
device = "cuda" if torch.cuda.is_available() else "cpu"
logging.info(f"Device set to {device}")

# Load the relevant model
model_ckpt = "google/pegasus-cnn_dailymail"
logging.info(f"Loading model and tokenizer from checkpoint {model_ckpt}")
tokenizer = AutoTokenizer.from_pretrained(model_ckpt)
model_pegasus = AutoModelForSeq2SeqLM.from_pretrained(model_ckpt).to(device)
logging.info(f"Model loaded: {model_pegasus}")

# Load dataset
logging.info("Loading empathetic_dialogues_summary dataset")
dataset_empathic = load_dataset("jtatman/empathetic_dialogues_summary")

# Splitting the dataset into train, validation, and test sets using the slicing API
train_valid_test_split = {
    'train': 'train[:80%]',
    'validation': 'train[80%:90%]',
    'test': 'train[90%:]'
}
dataset_empathic = load_dataset("jtatman/empathetic_dialogues_summary", split=train_valid_test_split)

logging.info(f"Split lengths: {[len(dataset_empathic[split]) for split in dataset_empathic]}")
logging.info(f"Features: {dataset_empathic['train'].column_names}")

# Logging a sample utterance and summary
logging.info("\nUtterance:")
logging.info(dataset_empathic["test"][1]["utterance"])
logging.info("\nSummary:")
logging.info(dataset_empathic["test"][1]["summary"])

# Summarization pipeline
pipe = pipeline('summarization', model=model_ckpt)
pipe_out = pipe(dataset_empathic['test'][0]['utterance'])
logging.info(pipe_out)
logging.info(pipe_out[0]['summary_text'].replace(" .", ".\n"))

# Calculate ROUGE scores
rouge_metric = load_metric('rouge')
score = calculate_metric_on_test_ds(dataset_empathic['test'], rouge_metric, model_pegasus, tokenizer, column_text='utterance', column_summary='summary', batch_size=8)

rouge_names = ["rouge1", "rouge2", "rougeL", "rougeLsum"]
rouge_dict = dict((rn, score[rn].mid.fmeasure) for rn in rouge_names)
dataframe_rouge = pd.DataFrame(rouge_dict, index=['pegasus'])
logging.info(dataframe_rouge)

# Token length histograms
utterance_token_len = [len(tokenizer.encode(s)) for s in dataset_empathic['train']['utterance']]
summary_token_len = [len(tokenizer.encode(s)) for s in dataset_empathic['train']['summary']]

fig, axes = plt.subplots(1, 2, figsize=(10, 4))
axes[0].hist(utterance_token_len, bins=20, color='C0', edgecolor='C0')
axes[0].set_title("Utterance Token Length")
axes[0].set_xlabel("Length")
axes[0].set_ylabel("Count")
axes[1].hist(summary_token_len, bins=20, color='C0', edgecolor='C0')
axes[1].set_title("Summary Token Length")
axes[1].set_xlabel("Length")
plt.tight_layout()
plt.show()

# save plot to empathetic folder
try:
    plt.savefig('empathetic/token_histogram.png')
except Exception as e:
    logging.error(f"Error saving token histogram plot: {e}")

# Convert examples to features
def convert_examples_to_features(example_batch):
    input_encodings = tokenizer(example_batch['utterance'], max_length=1024, truncation=True)

    with tokenizer.as_target_tokenizer():
        target_encodings = tokenizer(example_batch['summary'], max_length=128, truncation=True)

    target_encodings['labels'] = target_encodings['input_ids'].copy()
    target_encodings['input_ids'] = [[tokenizer.pad_token_id] + ids[:-1] for ids in target_encodings['input_ids']]

    return {
        'input_ids': input_encodings['input_ids'],
        'attention_mask': input_encodings['attention_mask'],
        'labels': target_encodings['input_ids']
    }

# Tokenize and collate data
dataset_empathic_pt = dataset_empathic.map(convert_examples_to_features, batched=True)
seq2seq_data_collator = DataCollatorForSeq2Seq(tokenizer, model=model_pegasus)

# Training arguments
logging.info("Setting up training arguments")
trainer_args = TrainingArguments(
    output_dir=os.path.expanduser('~/tm/tmgp/model-trainer/empathetic'),
    num_train_epochs=4,
    warmup_steps=500,
    per_device_train_batch_size=4,
    per_device_eval_batch_size=4,
    weight_decay=0.01,
    logging_steps=10,
    evaluation_strategy='steps',
    eval_steps=500,
    save_strategy='epoch',
    gradient_accumulation_steps=16
)
logging.info(f"Training arguments: {trainer_args}")

# Trainer
logging.info("Initialising trainer")
trainer = Trainer(model=model_pegasus, args=trainer_args,
                  tokenizer=tokenizer, data_collator=seq2seq_data_collator,
                  train_dataset=dataset_empathic_pt["train"],
                  eval_dataset=dataset_empathic_pt["validation"])

logging.info("Starting training on the empathic dataset")
trainer.train()
logging.info("Training complete on the empathic dataset")

# Calculate ROUGE scores after training
score = calculate_metric_on_test_ds(
    dataset_empathic['test'], rouge_metric, trainer.model, tokenizer, batch_size=2, column_text='utterance', column_summary='summary'
)
rouge_dict = dict((rn, score[rn].mid.fmeasure) for rn in rouge_names)
logging.info(pd.DataFrame(rouge_dict, index=[f'pegasus']))

# Save model and tokenizer
try:
    model_pegasus.save_pretrained(os.path.expanduser('~/tm/empathetic/tmgp/model-trainer/empathetic/model-2/model-2'))
except Exception as e:
    logging.error(f"Error saving model stage 1: {e}")

# Save model and tokenizer
try:
    trainer.model.save_pretrained(os.path.expanduser('~/tm/empathetic/tmgp/model-trainer/empathetic/model-1'))
    logging.info("Model saved successfully after empathic finetuning.")
except Exception as e:
    logging.error(f"Error saving model: {e}")

try:
    tokenizer.save_pretrained(os.path.expanduser('~/tm/empathetic/tmgp/model-trainer/empathetic/tokenizer'))
    logging.info("Tokenizer saved successfully after empathic finetuning.")
except Exception as e:
    logging.error(f"Error saving tokenizer: {e}")

# Summarization with fine-tuned model
sample_text = dataset_empathic["test"][0]["utterance"]
reference = dataset_empathic["test"][0]["summary"]
gen_kwargs = {"length_penalty": 0.8, "num_beams": 8, "max_length": 128}

try:
    pipe = pipeline("summarization", model=os.path.expanduser('~/tm/empathetic/tmgp/model-trainer/empathetic/model-1'), tokenizer=tokenizer)
    model_summary = pipe(sample_text, **gen_kwargs)[0]["summary_text"]
    logging.info("Utterance:")
    logging.info(sample_text)
    logging.info("\nReference Summary:")
    logging.info(reference)
    logging.info("\nModel Summary:")
    logging.info(model_summary)
except Exception as e:
    logging.error(f"Error during summarization: {e}")
