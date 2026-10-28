#!/usr/bin/env bash
# Remove every billable resource created by deploy.sh (keeps the ECR repo and IAM role).
#
#   AWS_PROFILE=koione ./deploy/ecs/teardown.sh
set -euo pipefail

APP="${APP:-sponsordesk}"
REGION="${AWS_REGION:-us-east-1}"
aws_() { aws --region "$REGION" --output text "$@"; }

aws_ ecs update-service --cluster "$APP" --service "$APP" --desired-count 0 >/dev/null 2>&1 || true
aws_ ecs delete-service --cluster "$APP" --service "$APP" --force >/dev/null 2>&1 || true
aws_ ecs wait services-inactive --cluster "$APP" --services "$APP" 2>/dev/null || true
aws_ ecs delete-cluster --cluster "$APP" >/dev/null 2>&1 || true

ALB_ARN="$(aws_ elbv2 describe-load-balancers --names "$APP" \
  --query 'LoadBalancers[0].LoadBalancerArn' 2>/dev/null || true)"
if [[ -n "$ALB_ARN" && "$ALB_ARN" != "None" ]]; then
  aws_ elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN"
  aws_ elbv2 wait load-balancers-deleted --load-balancer-arns "$ALB_ARN"
fi
TG_ARN="$(aws_ elbv2 describe-target-groups --names "$APP" \
  --query 'TargetGroups[0].TargetGroupArn' 2>/dev/null || true)"
[[ -n "$TG_ARN" && "$TG_ARN" != "None" ]] && aws_ elbv2 delete-target-group --target-group-arn "$TG_ARN"

VPC="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')"
sleep 20  # network interfaces release asynchronously
for name in "$APP-task" "$APP-alb"; do
  id="$(aws_ ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC" \
    Name=group-name,Values="$name" --query 'SecurityGroups[0].GroupId')"
  [[ "$id" != "None" ]] && aws_ ec2 delete-security-group --group-id "$id" || true
done
echo "==> Torn down. ECR repository '$APP' and the log group are kept."
