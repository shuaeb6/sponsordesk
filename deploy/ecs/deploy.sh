#!/usr/bin/env bash
# Deploy the SponsorDesk console to ECS Fargate (ARM64) behind an Application Load Balancer.
# Idempotent: safe to re-run; later runs push a new image and roll the service.
#
#   AWS_PROFILE=koione ./deploy/ecs/deploy.sh
set -euo pipefail

APP="${APP:-sponsordesk}"
REGION="${AWS_REGION:-us-east-1}"
CPU="${CPU:-512}"
MEMORY="${MEMORY:-1024}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
aws_() { aws --region "$REGION" --output text "$@"; }

ACCOUNT="$(aws_ sts get-caller-identity --query Account)"
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$APP"
echo "==> Account $ACCOUNT · region $REGION · image $REPO:$TAG"

# --- Image ---------------------------------------------------------------------------
aws_ ecr describe-repositories --repository-names "$APP" >/dev/null 2>&1 \
  || aws_ ecr create-repository --repository-name "$APP" \
       --image-scanning-configuration scanOnPush=true >/dev/null
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com" >/dev/null
docker buildx build --platform linux/arm64 -f "$ROOT/docker/console.Dockerfile" \
  -t "$REPO:$TAG" -t "$REPO:latest" --push "$ROOT"

# --- IAM and logs --------------------------------------------------------------------
ROLE=ecsTaskExecutionRole
if ! aws_ iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws_ iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws_ iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  sleep 10
fi
ROLE_ARN="$(aws_ iam get-role --role-name "$ROLE" --query Role.Arn)"
aws_ logs create-log-group --log-group-name "/ecs/$APP" 2>/dev/null || true
aws_ logs put-retention-policy --log-group-name "/ecs/$APP" --retention-in-days 14

# --- Network -------------------------------------------------------------------------
VPC="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')"
SUBNETS="$(aws_ ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" \
  Name=default-for-az,Values=true --query 'Subnets[].SubnetId' | tr '\t' ',')"
sg() {
  local name="$1" desc="$2" id
  id="$(aws_ ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC" \
    Name=group-name,Values="$name" --query 'SecurityGroups[0].GroupId')"
  if [[ "$id" == "None" ]]; then
    id="$(aws_ ec2 create-security-group --vpc-id "$VPC" --group-name "$name" \
      --description "$desc" --query GroupId)"
  fi
  echo "$id"
}
ALB_SG="$(sg "$APP-alb" "SponsorDesk load balancer")"
TASK_SG="$(sg "$APP-task" "SponsorDesk tasks")"
aws_ ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp --port 80 \
  --cidr 0.0.0.0/0 >/dev/null 2>&1 || true
aws_ ec2 authorize-security-group-ingress --group-id "$TASK_SG" --protocol tcp --port 8000 \
  --source-group "$ALB_SG" >/dev/null 2>&1 || true

# --- Load balancer -------------------------------------------------------------------
ALB_ARN="$(aws_ elbv2 describe-load-balancers --names "$APP" \
  --query 'LoadBalancers[0].LoadBalancerArn' 2>/dev/null || true)"
if [[ -z "$ALB_ARN" || "$ALB_ARN" == "None" ]]; then
  ALB_ARN="$(aws_ elbv2 create-load-balancer --name "$APP" --type application \
    --scheme internet-facing --security-groups "$ALB_SG" \
    --subnets $(echo "$SUBNETS" | tr ',' ' ') --query 'LoadBalancers[0].LoadBalancerArn')"
fi
TG_ARN="$(aws_ elbv2 describe-target-groups --names "$APP" \
  --query 'TargetGroups[0].TargetGroupArn' 2>/dev/null || true)"
if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
  TG_ARN="$(aws_ elbv2 create-target-group --name "$APP" --protocol HTTP --port 8000 \
    --vpc-id "$VPC" --target-type ip --health-check-path /api/health \
    --health-check-interval-seconds 15 --healthy-threshold-count 2 \
    --query 'TargetGroups[0].TargetGroupArn')"
  aws_ elbv2 modify-target-group-attributes --target-group-arn "$TG_ARN" \
    --attributes Key=deregistration_delay.timeout_seconds,Value=15 >/dev/null
fi
if [[ "$(aws_ elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" \
      --query 'length(Listeners)')" == "0" ]]; then
  aws_ elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTP --port 80 \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
fi

# --- ECS -----------------------------------------------------------------------------
aws_ ecs describe-clusters --clusters "$APP" --query 'clusters[?status==`ACTIVE`].clusterName' \
  | grep -q "$APP" || aws_ ecs create-cluster --cluster-name "$APP" >/dev/null

TASK_DEF="$(aws_ ecs register-task-definition --family "$APP" \
  --requires-compatibilities FARGATE --network-mode awsvpc --cpu "$CPU" --memory "$MEMORY" \
  --runtime-platform cpuArchitecture=ARM64,operatingSystemFamily=LINUX \
  --execution-role-arn "$ROLE_ARN" \
  --container-definitions "[{
    \"name\": \"console\", \"image\": \"$REPO:$TAG\", \"essential\": true,
    \"portMappings\": [{\"containerPort\": 8000, \"protocol\": \"tcp\"}],
    \"environment\": [{\"name\": \"SYNTH_SEED\", \"value\": \"42\"}],
    \"readonlyRootFilesystem\": false,
    \"logConfiguration\": {\"logDriver\": \"awslogs\", \"options\": {
      \"awslogs-group\": \"/ecs/$APP\", \"awslogs-region\": \"$REGION\",
      \"awslogs-stream-prefix\": \"console\"}}
  }]" --query 'taskDefinition.taskDefinitionArn')"

NETWORK="awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$TASK_SG],assignPublicIp=ENABLED}"
if [[ "$(aws_ ecs describe-services --cluster "$APP" --services "$APP" \
      --query 'services[?status==`ACTIVE`] | length(@)')" == "0" ]]; then
  aws_ ecs create-service --cluster "$APP" --service-name "$APP" --launch-type FARGATE \
    --task-definition "$TASK_DEF" --desired-count 1 --network-configuration "$NETWORK" \
    --health-check-grace-period-seconds 60 \
    --deployment-configuration "deploymentCircuitBreaker={enable=true,rollback=true}" \
    --load-balancers "targetGroupArn=$TG_ARN,containerName=console,containerPort=8000" >/dev/null
else
  aws_ ecs update-service --cluster "$APP" --service "$APP" --task-definition "$TASK_DEF" \
    --force-new-deployment >/dev/null
fi

echo "==> Waiting for the service to become stable (2-5 minutes)"
aws_ ecs wait services-stable --cluster "$APP" --services "$APP"
DNS="$(aws_ elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName')"
echo "==> Live at http://$DNS"
