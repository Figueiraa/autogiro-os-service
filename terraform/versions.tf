terraform {
  required_version = ">= 1.5"

  required_providers {
    neon = {
      source  = "kislerdm/neon"
      version = "~> 0.14"
    }
    # Cria o usuário da Lambda conectado como o usuário do serviço (main.tf).
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.25"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
