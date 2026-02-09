/**
 * Editor - Pozotron-style annotation review interface
 *
 * Keyboard shortcuts:
 *   A     = Mark current annotation as OK
 *   D     = Mark current annotation as Needs Edit
 *   Space = Play segment audio
 *   Up    = Previous annotation
 *   Down  = Next annotation
 *   1-9   = Select take number
 */

(function() {
    'use strict';

    const audioPlayer = document.getElementById('audioPlayer');
    const annotationList = document.getElementById('annotationList');
    const takeSection = document.getElementById('takeSection');
    const takeList = document.getElementById('takeList');
    const segmentInfo = document.getElementById('segmentInfo');
    const manuscriptView = document.getElementById('manuscriptView');

    let currentAnnotationIndex = -1;
    let visibleAnnotations = [];
    let isPlaying = false;
    let playTimeout = null;

    // ========================
    // Initialization
    // ========================

    function init() {
        updateVisibleAnnotations();
        bindAnnotationClicks();
        bindWordClicks();
        bindFilterChanges();
        bindBatchActions();
        bindKeyboard();
        pollProcessingStatus();

        // Select first annotation if available
        if (visibleAnnotations.length > 0) {
            selectAnnotation(0);
        }
    }

    // ========================
    // Annotation Navigation
    // ========================

    function updateVisibleAnnotations() {
        const typeFilter = document.getElementById('filterType').value;
        const statusFilter = document.getElementById('filterStatus').value;

        document.querySelectorAll('.annotation-item').forEach(function(item) {
            const matchType = !typeFilter || item.dataset.conflictType === typeFilter;
            const matchStatus = !statusFilter || item.dataset.status === statusFilter;
            item.style.display = (matchType && matchStatus) ? '' : 'none';
        });

        visibleAnnotations = Array.from(
            document.querySelectorAll('.annotation-item:not([style*="display: none"])')
        );

        document.getElementById('annotationCount').textContent = visibleAnnotations.length;
    }

    function selectAnnotation(index) {
        if (index < 0 || index >= visibleAnnotations.length) return;

        // Deselect previous
        visibleAnnotations.forEach(a => a.classList.remove('active'));
        document.querySelectorAll('.word-span.active').forEach(w => w.classList.remove('active'));

        currentAnnotationIndex = index;
        const item = visibleAnnotations[index];
        item.classList.add('active');

        // Scroll annotation into view
        item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

        // Highlight corresponding word in manuscript
        const segmentId = item.dataset.segmentId;
        const wordSpan = manuscriptView.querySelector('.word-span[data-segment-id="' + segmentId + '"]');
        if (wordSpan) {
            wordSpan.classList.add('active');
            wordSpan.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }

        // Update details panel
        updateSegmentDetails(item, segmentId);
        updateTakePanel(segmentId);
    }

    function nextAnnotation() {
        if (currentAnnotationIndex < visibleAnnotations.length - 1) {
            selectAnnotation(currentAnnotationIndex + 1);
        }
    }

    function previousAnnotation() {
        if (currentAnnotationIndex > 0) {
            selectAnnotation(currentAnnotationIndex - 1);
        }
    }

    // ========================
    // Segment Details
    // ========================

    function updateSegmentDetails(annotationItem, segmentId) {
        const wordSpan = manuscriptView.querySelector('.word-span[data-segment-id="' + segmentId + '"]');
        if (!wordSpan) {
            segmentInfo.style.display = 'none';
            return;
        }

        segmentInfo.style.display = '';

        const content = annotationItem.querySelector('.annotation-content');
        const detected = content ? (content.querySelector('.detected')?.textContent || '-') : '-';
        const expected = content ? (content.querySelector('.expected')?.textContent || '-') : '-';

        document.getElementById('infoDetected').textContent = detected;
        document.getElementById('infoExpected').textContent = expected;
        document.getElementById('infoTime').textContent =
            parseFloat(wordSpan.dataset.start).toFixed(2) + 's - ' +
            parseFloat(wordSpan.dataset.end).toFixed(2) + 's';
        document.getElementById('infoConfidence').textContent =
            (parseFloat(wordSpan.dataset.confidence) * 100).toFixed(0) + '%';
    }

    // ========================
    // Takes
    // ========================

    function updateTakePanel(segmentId) {
        const segId = parseInt(segmentId);
        const takes = window.TAKE_DATA[segId];

        if (!takes || takes.length <= 1) {
            takeSection.style.display = 'none';
            return;
        }

        takeSection.style.display = '';
        takeList.innerHTML = '';

        takes.forEach(function(take) {
            const item = document.createElement('div');
            item.className = 'take-item' + (take.isSelected ? ' selected' : '');
            item.dataset.takeId = take.id;
            item.innerHTML =
                '<span class="take-number">' + take.takeNumber + '</span>' +
                '<span>Take ' + take.takeNumber + '</span>' +
                '<span class="take-confidence">' + (take.confidence * 100).toFixed(0) + '%</span>' +
                '<button class="take-play-btn" title="Play this take">' +
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>' +
                '</button>';

            // Click to select take
            item.addEventListener('click', function(e) {
                if (e.target.closest('.take-play-btn')) {
                    playAudioSegment(take.startTime, take.endTime, take.audioFileId);
                    return;
                }
                selectTake(take.id, segId);
            });

            takeList.appendChild(item);
        });
    }

    function selectTake(takeId, segmentId) {
        fetch('/api/take/' + takeId + '/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        })
        .then(r => r.json())
        .then(function(data) {
            // Update local take data
            const takes = window.TAKE_DATA[segmentId];
            if (takes) {
                takes.forEach(t => t.isSelected = (t.id === takeId));
            }
            // Refresh take panel
            updateTakePanel(segmentId);
        })
        .catch(err => console.error('Failed to select take:', err));
    }

    // ========================
    // Audio Playback
    // ========================

    function playCurrentSegment() {
        if (currentAnnotationIndex < 0 || !visibleAnnotations[currentAnnotationIndex]) return;

        const item = visibleAnnotations[currentAnnotationIndex];
        const segmentId = item.dataset.segmentId;
        const wordSpan = manuscriptView.querySelector('.word-span[data-segment-id="' + segmentId + '"]');

        if (wordSpan && audioPlayer) {
            const start = parseFloat(wordSpan.dataset.start);
            const end = parseFloat(wordSpan.dataset.end);
            const audioId = wordSpan.dataset.audioId;
            playAudioSegment(start, end, audioId);
        }
    }

    function playAudioSegment(startTime, endTime, audioFileId) {
        if (!audioPlayer) return;

        // Check if we need to switch audio source
        if (audioFileId && window.AUDIO_FILES[audioFileId]) {
            const currentSrc = audioPlayer.querySelector('source');
            const targetUrl = window.AUDIO_FILES[audioFileId].url;
            if (currentSrc && currentSrc.src !== window.location.origin + targetUrl) {
                currentSrc.src = targetUrl;
                audioPlayer.load();
            }
        }

        // Clear any pending stop
        if (playTimeout) {
            clearTimeout(playTimeout);
            playTimeout = null;
        }

        audioPlayer.currentTime = startTime;
        audioPlayer.play();
        isPlaying = true;

        // Stop at end time
        const duration = (endTime - startTime) * 1000;
        if (duration > 0) {
            playTimeout = setTimeout(function() {
                audioPlayer.pause();
                isPlaying = false;
            }, duration + 100);
        }
    }

    // ========================
    // Conflict Status Updates
    // ========================

    function updateConflictStatus(conflictId, newStatus) {
        fetch('/api/conflict/' + conflictId + '/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
        })
        .then(r => r.json())
        .then(function(data) {
            // Update the annotation item
            const item = document.querySelector('.annotation-item[data-conflict-id="' + conflictId + '"]');
            if (item) {
                item.dataset.status = newStatus;
                item.className = 'annotation-item annotation-' + newStatus;
                if (item === visibleAnnotations[currentAnnotationIndex]) {
                    item.classList.add('active');
                }

                // Update action buttons
                item.querySelectorAll('.action-btn').forEach(b => b.classList.remove('active'));
                const activeBtn = item.querySelector('.action-btn[data-action="' + newStatus + '"]');
                if (activeBtn) activeBtn.classList.add('active');
            }

            // Update stats
            if (data.conflict_stats) {
                updateStats(data.conflict_stats);
            }

            // Auto-advance to next annotation
            nextAnnotation();
        })
        .catch(err => console.error('Failed to update conflict:', err));
    }

    function updateStats(stats) {
        document.getElementById('statTotal').textContent = stats.total;
        document.getElementById('statResolved').textContent = stats.resolved;
        document.getElementById('statPending').textContent = stats.pending;
    }

    // ========================
    // Batch Operations
    // ========================

    function batchUpdate(status) {
        const ids = visibleAnnotations
            .filter(a => a.dataset.status === 'pending')
            .map(a => parseInt(a.dataset.conflictId));

        if (ids.length === 0) return;

        if (!confirm('Mark ' + ids.length + ' annotations as ' + status.replace('_', ' ') + '?')) return;

        fetch('/api/conflict/batch-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conflict_ids: ids, status: status }),
        })
        .then(r => r.json())
        .then(function(data) {
            // Reload to reflect changes
            window.location.reload();
        })
        .catch(err => console.error('Batch update failed:', err));
    }

    // ========================
    // Event Bindings
    // ========================

    function bindAnnotationClicks() {
        annotationList.addEventListener('click', function(e) {
            const item = e.target.closest('.annotation-item');
            if (!item) return;

            // Check if an action button was clicked
            const actionBtn = e.target.closest('.action-btn');
            if (actionBtn) {
                const action = actionBtn.dataset.action;
                if (action === 'play') {
                    // Select this annotation first
                    const idx = visibleAnnotations.indexOf(item);
                    if (idx >= 0) selectAnnotation(idx);
                    playCurrentSegment();
                } else {
                    updateConflictStatus(parseInt(item.dataset.conflictId), action);
                }
                return;
            }

            // Regular click - select annotation
            const idx = visibleAnnotations.indexOf(item);
            if (idx >= 0) selectAnnotation(idx);
        });
    }

    function bindWordClicks() {
        manuscriptView.addEventListener('click', function(e) {
            const wordSpan = e.target.closest('.word-span');
            if (!wordSpan) return;

            const segmentId = wordSpan.dataset.segmentId;

            // Find matching annotation
            const matchingAnnotation = visibleAnnotations.find(
                a => a.dataset.segmentId === segmentId
            );
            if (matchingAnnotation) {
                const idx = visibleAnnotations.indexOf(matchingAnnotation);
                selectAnnotation(idx);
            } else {
                // No conflict - just play the audio
                document.querySelectorAll('.word-span.active').forEach(w => w.classList.remove('active'));
                wordSpan.classList.add('active');

                if (audioPlayer) {
                    const start = parseFloat(wordSpan.dataset.start);
                    const end = parseFloat(wordSpan.dataset.end);
                    playAudioSegment(start, end, wordSpan.dataset.audioId);
                }
            }

            // Show takes if available
            updateTakePanel(segmentId);
        });
    }

    function bindFilterChanges() {
        document.getElementById('filterType').addEventListener('change', function() {
            updateVisibleAnnotations();
            currentAnnotationIndex = -1;
            if (visibleAnnotations.length > 0) selectAnnotation(0);
        });

        document.getElementById('filterStatus').addEventListener('change', function() {
            updateVisibleAnnotations();
            currentAnnotationIndex = -1;
            if (visibleAnnotations.length > 0) selectAnnotation(0);
        });
    }

    function bindBatchActions() {
        document.getElementById('markAllOk').addEventListener('click', function() {
            batchUpdate('ok');
        });
        document.getElementById('markAllEdit').addEventListener('click', function() {
            batchUpdate('needs_edit');
        });
    }

    function bindKeyboard() {
        document.addEventListener('keydown', function(e) {
            // Don't capture when typing in inputs
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
                return;
            }

            switch(e.key) {
                case 'a':
                case 'A':
                    e.preventDefault();
                    if (currentAnnotationIndex >= 0) {
                        const item = visibleAnnotations[currentAnnotationIndex];
                        updateConflictStatus(parseInt(item.dataset.conflictId), 'ok');
                    }
                    break;

                case 'd':
                case 'D':
                    e.preventDefault();
                    if (currentAnnotationIndex >= 0) {
                        const item = visibleAnnotations[currentAnnotationIndex];
                        updateConflictStatus(parseInt(item.dataset.conflictId), 'needs_edit');
                    }
                    break;

                case ' ':
                    e.preventDefault();
                    if (isPlaying && audioPlayer) {
                        audioPlayer.pause();
                        isPlaying = false;
                        if (playTimeout) {
                            clearTimeout(playTimeout);
                            playTimeout = null;
                        }
                    } else {
                        playCurrentSegment();
                    }
                    break;

                case 'ArrowDown':
                    e.preventDefault();
                    nextAnnotation();
                    break;

                case 'ArrowUp':
                    e.preventDefault();
                    previousAnnotation();
                    break;

                case '1': case '2': case '3': case '4': case '5':
                case '6': case '7': case '8': case '9':
                    e.preventDefault();
                    selectTakeByNumber(parseInt(e.key));
                    break;
            }
        });
    }

    function selectTakeByNumber(num) {
        if (currentAnnotationIndex < 0) return;
        const item = visibleAnnotations[currentAnnotationIndex];
        const segmentId = parseInt(item.dataset.segmentId);
        const takes = window.TAKE_DATA[segmentId];
        if (takes && takes[num - 1]) {
            selectTake(takes[num - 1].id, segmentId);
        }
    }

    // ========================
    // Processing Status Poll
    // ========================

    function pollProcessingStatus() {
        const layout = document.querySelector('.editor-layout');
        const projectId = layout ? layout.dataset.projectId : null;
        if (!projectId) return;

        // Check status badge
        const badge = document.querySelector('.status-processing');
        if (!badge) return;

        const interval = setInterval(function() {
            fetch('/api/project/' + projectId + '/status')
                .then(r => r.json())
                .then(function(data) {
                    if (data.status !== 'processing') {
                        clearInterval(interval);
                        window.location.reload();
                    }
                })
                .catch(() => {});
        }, 3000);
    }

    // ========================
    // Audio Player Time Display
    // ========================

    if (audioPlayer) {
        audioPlayer.addEventListener('timeupdate', function() {
            const current = document.getElementById('currentTime');
            if (current) {
                const t = audioPlayer.currentTime;
                const min = Math.floor(t / 60);
                const sec = Math.floor(t % 60);
                current.textContent = min + ':' + (sec < 10 ? '0' : '') + sec;
            }

            // Highlight current word during playback
            if (isPlaying) {
                const currentTime = audioPlayer.currentTime;
                document.querySelectorAll('.word-span').forEach(function(span) {
                    const start = parseFloat(span.dataset.start);
                    const end = parseFloat(span.dataset.end);
                    if (currentTime >= start && currentTime <= end) {
                        span.classList.add('playing');
                    } else {
                        span.classList.remove('playing');
                    }
                });
            }
        });

        audioPlayer.addEventListener('ended', function() {
            isPlaying = false;
        });
    }

    // ========================
    // Start
    // ========================

    init();

})();
