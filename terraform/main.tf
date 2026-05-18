# HarnessIT substrate EC2 instance.
#
# Provisions one t3.xlarge Ubuntu 24.04 host in the default VPC of the
# configured AWS account (us-east-1) for running containerlab + SONiC.
# Access is SSM Session Manager only — no SSH, no inbound SG rules.
#
# The instance is intentionally dedicated to HarnessIT substrate work
# and separate from any other projects sharing the same AWS account.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use (named profile in ~/.aws/credentials)"
  type        = string
  default     = "sniachat"
}

variable "instance_type" {
  description = "EC2 instance type — t3.xlarge fits 6 SONiC + 8 host topology"
  type        = string
  default     = "t3.xlarge"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GiB"
  type        = number
  default     = 50
}

# --- Data sources: discover existing AWS resources by attribute ---

# Default VPC — separate from RackIT VPC for clean isolation
data "aws_vpc" "default" {
  default = true
}

# First public subnet in the default VPC (any AZ is fine for a single-host lab)
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Latest Ubuntu 24.04 Noble Numbat AMI from Canonical
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# --- IAM: role + instance profile for SSM Session Manager ---

resource "aws_iam_role" "ssm" {
  name = "harnessit-substrate-ssm-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  name = "harnessit-substrate-ssm-profile"
  role = aws_iam_role.ssm.name
  tags = local.tags
}

# --- Security group: no inbound, all outbound (default outbound is all) ---

resource "aws_security_group" "substrate" {
  name        = "harnessit-substrate-sg"
  description = "HarnessIT substrate host - SSM-only access, no inbound"
  vpc_id      = data.aws_vpc.default.id

  # Explicit egress all (required for AWS SSM endpoint reachability,
  # git clone over HTTPS, docker pull, apt update, etc.)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound - required for SSM, git, docker pull, apt"
  }

  tags = local.tags
}

# --- The instance ---

resource "aws_instance" "substrate" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_type
  subnet_id     = tolist(data.aws_subnets.default.ids)[0]

  vpc_security_group_ids = [aws_security_group.substrate.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name

  # Required so the SSM agent (pre-installed in Ubuntu 24.04 Canonical
  # AMIs) can reach the SSM endpoint from a public subnet. The default
  # VPC's IGW provides the outbound route.
  associate_public_ip_address = true

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  # Friendlier resource ID + metadata service v2 only (IMDSv1 disabled
  # for safer credential exposure)
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  tags = merge(local.tags, { Name = "harnessit-substrate" })

  # Don't replace the instance just because Canonical published a
  # newer AMI — only replace when the user opts in.
  lifecycle {
    ignore_changes = [ami]
  }
}

locals {
  tags = {
    Project = "HarnessIT"
    Owner   = "provandal-dev"
    Repo    = "provandal/containerlab-adapter"
    Managed = "terraform"
  }
}

# --- Outputs ---

output "instance_id" {
  description = "EC2 instance ID for SSM target"
  value       = aws_instance.substrate.id
}

output "public_dns" {
  description = "Public DNS (informational — access is via SSM, not SSH)"
  value       = aws_instance.substrate.public_dns
}

output "ssm_session_command" {
  description = "Open an interactive SSM session (requires session-manager-plugin)"
  value       = "aws --profile ${var.aws_profile} ssm start-session --target ${aws_instance.substrate.id}"
}

output "ssm_send_command_example" {
  description = "Run a one-shot command (no plugin needed)"
  value       = "aws --profile ${var.aws_profile} ssm send-command --instance-ids ${aws_instance.substrate.id} --document-name AWS-RunShellScript --parameters 'commands=[\"uname -a\"]'"
}
