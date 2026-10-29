# TFLint config for every root module under infra/.
# Run from infra/: tflint --init && tflint --recursive --config "$(pwd)/.tflint.hcl"

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.41.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}
