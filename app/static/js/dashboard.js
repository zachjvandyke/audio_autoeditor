document.addEventListener('DOMContentLoaded', function() {
    // Auto-refresh processing projects
    const processingCards = document.querySelectorAll('.processing-indicator');
    if (processingCards.length > 0) {
        setInterval(function() {
            processingCards.forEach(function(indicator) {
                const card = indicator.closest('.project-card');
                const projectId = card.dataset.projectId;
                fetch('/api/project/' + projectId + '/status')
                    .then(r => r.json())
                    .then(data => {
                        if (data.status !== 'processing') {
                            window.location.reload();
                        }
                    })
                    .catch(() => {});
            });
        }, 3000);
    }
});
