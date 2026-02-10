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
    let currentAudioFileId = null;
    let cachedWordSpans = null;

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

    function isConfidenceVisible() {
        var cb = document.getElementById('showConfidence');
        return cb && cb.checked;
    }

    function updateVisibleAnnotations() {
        const typeFilter = document.getElementById('filterType').value;
        const statusFilter = document.getElementById('filterStatus').value;
        const showConf = isConfidenceVisible();

        document.querySelectorAll('.annotation-item').forEach(function(item) {
            const matchType = !typeFilter || item.dataset.conflictType === typeFilter;
            const matchStatus = !statusFilter || item.dataset.status === statusFilter;
            // Hide low_confidence unless the confidence toggle is on
            const hideConf = !showConf && item.dataset.conflictType === 'low_confidence';
            item.style.display = (matchType && matchStatus && !hideConf) ? '' : 'none';
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
            const audioId = wordSpan.dataset.audioId;
            playAudioSegment(start, null, audioId);
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

        clearPlayingHighlights();
        currentAudioFileId = audioFileId || null;
        audioPlayer.currentTime = startTime;
        audioPlayer.play();
        isPlaying = true;

        // Only auto-stop if an explicit end time is provided (e.g. take playback).
        // Normal playback continues until the user pauses.
        if (endTime != null) {
            const duration = (endTime - startTime) * 1000;
            if (duration > 0) {
                playTimeout = setTimeout(function() {
                    audioPlayer.pause();
                    isPlaying = false;
                    clearPlayingHighlights();
                }, duration + 100);
            }
        }
    }

    function getWordSpans() {
        if (!cachedWordSpans) {
            cachedWordSpans = Array.from(document.querySelectorAll('.word-span'));
        }
        return cachedWordSpans;
    }

    function clearPlayingHighlights() {
        getWordSpans().forEach(function(span) {
            span.classList.remove('playing');
        });
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
                    playAudioSegment(start, null, wordSpan.dataset.audioId);
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

        document.getElementById('showConfidence').addEventListener('change', function() {
            var layout = document.querySelector('.editor-layout');
            if (this.checked) {
                layout.classList.remove('confidence-hidden');
            } else {
                layout.classList.add('confidence-hidden');
            }
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
                        // pause event handler cleans up isPlaying, timeout, and highlights
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

        // Check if there are any processing/pending chapters or project is processing
        const badge = document.querySelector('.status-processing');
        const processingTabs = document.querySelectorAll('.chapter-tab[data-processing-status="processing"], .chapter-tab[data-processing-status="pending"]');
        if (!badge && processingTabs.length === 0) return;

        const interval = setInterval(function() {
            fetch('/api/project/' + projectId + '/chapter-status')
                .then(r => r.json())
                .then(function(data) {
                    let anyProcessing = false;

                    // Update individual chapter tabs
                    data.chapters.forEach(function(ch) {
                        const tab = document.querySelector('.chapter-tab[data-chapter-id="' + ch.id + '"]');
                        if (!tab) return;

                        const prevStatus = tab.dataset.processingStatus;
                        tab.dataset.processingStatus = ch.processing_status;

                        if (ch.processing_status === 'processing' || ch.processing_status === 'pending') {
                            anyProcessing = true;
                        }

                        // If a chapter just became ready, update its tab appearance
                        if (prevStatus !== 'ready' && ch.processing_status === 'ready') {
                            tab.classList.remove('chapter-tab-processing');
                            // Remove spinner/pending badge
                            const spinner = tab.querySelector('.spinner-sm');
                            if (spinner) spinner.remove();
                            const pendingBadge = tab.querySelector('.chapter-tab-pending');
                            if (pendingBadge) pendingBadge.remove();
                        }

                        if (ch.processing_status === 'processing') {
                            tab.classList.add('chapter-tab-processing');
                            if (!tab.querySelector('.spinner-sm')) {
                                const sp = document.createElement('span');
                                sp.className = 'spinner spinner-sm';
                                tab.insertBefore(sp, tab.firstChild);
                            }
                        }
                    });

                    // If project is done and we're viewing a chapter that was processing, reload
                    if (data.project_status !== 'processing' && !anyProcessing) {
                        clearInterval(interval);
                        // Update the status badge
                        const statusBadge = document.querySelector('.status-badge');
                        if (statusBadge) {
                            statusBadge.className = 'status-badge status-' + data.project_status;
                            statusBadge.textContent = data.project_status;
                        }
                        // Reload to get the final data
                        window.location.reload();
                    }

                    // If the active chapter just finished, reload to get its segments
                    const activeTab = document.querySelector('.chapter-tab.active');
                    if (activeTab) {
                        const activeChapter = data.chapters.find(function(ch) {
                            return String(ch.id) === activeTab.dataset.chapterId;
                        });
                        if (activeChapter && activeTab._prevProcessingStatus === 'processing' && activeChapter.processing_status === 'ready') {
                            window.location.reload();
                        }
                        if (activeChapter) {
                            activeTab._prevProcessingStatus = activeChapter.processing_status;
                        }
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
                getWordSpans().forEach(function(span) {
                    const start = parseFloat(span.dataset.start);
                    const end = parseFloat(span.dataset.end);
                    // Only highlight words from the currently playing audio file
                    if (currentAudioFileId && span.dataset.audioId !== currentAudioFileId) {
                        span.classList.remove('playing');
                    // Use < for end to prevent two adjacent words highlighting simultaneously
                    } else if (currentTime >= start && currentTime < end) {
                        span.classList.add('playing');
                    } else {
                        span.classList.remove('playing');
                    }
                });
            }
        });

        audioPlayer.addEventListener('ended', function() {
            isPlaying = false;
            clearPlayingHighlights();
        });

        audioPlayer.addEventListener('pause', function() {
            isPlaying = false;
            if (playTimeout) {
                clearTimeout(playTimeout);
                playTimeout = null;
            }
            clearPlayingHighlights();
        });
    }

    // ========================
    // Start
    // ========================

    init();

})();
