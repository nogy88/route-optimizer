module.exports = {
  apps: [{
    name: 'route-optimizer-frontend',
    cwd: './',
    script: 'node dist/main.js',
    instances: 1,
    autorestart: true,
    watch: false
  }],
  deploy: {
    dev: {
      user: 'ubuntu',
      host: '54.191.84.231',
      ref: 'origin/main',
    //   key: '~/.ssh/qcomm-dev',
      repo: 'https://github.com/goruden/route-optimizer.git',
      path: '/opt/route-optimizer',
      'pre-setup': 'sudo mkdir -p /opt/route-optimizer && sudo chown -R ubuntu:ubuntu /opt/route-optimizer',
      'post-deploy': 'pnpm i && pnpm build && cd ./apps/route-optimizer && pm2 reload ecosystem.config.js'
    }
  }
}