// ---- Jenkinsfile ----
// This is the CI/CD pipeline that builds and deploys DevGordon itself.
// When you push to git, Jenkins:
//   1. Checks out the code
//   2. Scans it with SonarQube (quality gate)
//   3. Builds the Docker image
//   4. Runs Ansible to deploy it to Kubernetes (Minikube)
//
// This Jenkinsfile is also what the demo shows as the "pipeline 50%" part.
// You trigger it from the DevGordon UI itself — that's the full loop.

pipeline {
    agent any

    // These environment variables are set in Jenkins > Manage Jenkins > Credentials
    environment {
        SONAR_TOKEN      = credentials('sonarqube-token')
        DOCKER_IMAGE     = "devgordon"
        DOCKER_TAG       = "${BUILD_NUMBER}"    // Each build gets a unique tag
        KUBECONFIG       = "/var/jenkins_home/.kube/config"
    }

    stages {

        stage('Checkout') {
            steps {
                // Pull latest code from git
                // In a real setup this triggers automatically via webhook
                checkout scm
                echo "Building commit: ${env.GIT_COMMIT?.take(8) ?: 'local'}"
            }
        }

        stage('SonarQube Analysis') {
            // SonarQube scans your Python source code for:
            // - Security vulnerabilities (SQL injection, hardcoded secrets)
            // - Code smells (overly complex functions, duplicated code)
            // - Bugs (unreachable code, null dereferences)
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh '''
                        sonar-scanner \
                          -Dsonar.projectKey=devgordon \
                          -Dsonar.projectName="DevGordon" \
                          -Dsonar.sources=app \
                          -Dsonar.language=py \
                          -Dsonar.python.version=3.11 \
                          -Dsonar.host.url=${SONAR_HOST_URL} \
                          -Dsonar.token=${SONAR_TOKEN}
                    '''
                }
            }
        }

        stage('Quality Gate') {
            // Pipeline STOPS here if SonarQube finds critical issues.
            // This is what makes SonarQube a "gate" not just a report.
            steps {
                timeout(time: 5, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Docker Build') {
            steps {
                sh """
                    docker build \
                      -t ${DOCKER_IMAGE}:${DOCKER_TAG} \
                      -t ${DOCKER_IMAGE}:latest \
                      -f Dockerfile \
                      ./app
                """
                echo "Built image: ${DOCKER_IMAGE}:${DOCKER_TAG}"
            }
        }

        stage('Ansible Deploy') {
            // Ansible applies the Kubernetes manifests.
            // Using Ansible (not raw kubectl) here is intentional —
            // it means Jenkins doesn't need kubectl configured, only Ansible does.
            // And Ansible is idempotent: running it twice gives the same result.
            steps {
                sh """
                    ansible-playbook ansible/deploy.yml \
                      -i ansible/inventory.ini \
                      --extra-vars "image_tag=${DOCKER_TAG} app_name=${DOCKER_IMAGE}"
                """
            }
        }

        stage('Verify Deployment') {
            // Quick sanity check: did our pods actually come up?
            steps {
                sh """
                    kubectl rollout status deployment/devgordon -n devgordon --timeout=120s
                    kubectl get pods -n devgordon
                """
            }
        }
    }

    post {
        success {
            echo "✓ DevGordon deployed successfully — build #${BUILD_NUMBER}"
        }
        failure {
            echo "✗ Pipeline failed at stage: ${currentBuild.currentResult}"
            // In a real project you'd send a Slack/email notification here
        }
        always {
            // Clean up Docker build artifacts
            sh "docker image prune -f || true"
        }
    }
}
