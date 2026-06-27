/**
 * Enhanced VAD Configuration for Natural Conversation
 * 
 * Optimized thresholds and parameters for the most natural,
 * human-like conversation flow.
 */

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// OPTIMIZED VAD THRESHOLDS (Sweet Spot)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

// After extensive testing, these values provide the best balance:
// - Not too sensitive (avoiding false triggers from background noise)
// - Not too insensitive (capturing natural speech starts)
// - Smooth turn-taking without cutting off users mid-sentence

window.VAD_CONFIG = {
    // Speech detection thresholds
    // Lower positive threshold = more sensitive (catches quieter speech)
    // Higher negative threshold = less sensitive to silence
    positiveSpeechThreshold: 0.48,  // Slightly lower than original 0.50 for better capture
    negativeSpeechThreshold: 0.33,  // Slightly lower than original 0.35 for cleaner silence detection
    
    // Echo guard (when SARA is speaking)
    // This needs to be high enough to block self-TTS but low enough for genuine interruptions
    echoGuardThreshold: 0.70,  // Slightly lower than original 0.72 for more responsive barge-in
    
    // Timing parameters
    // These control the "feel" of the conversation
    preSpeechPadMs: 650,      // Slightly longer pre-roll (from 600ms) - never miss first syllable
    minSpeechMs: 180,         // Slightly shorter confirm window (from 200ms) - feel more responsive
    redemptionMs: 850,        // Slightly shorter base silence (from 900ms) - quicker responses
    
    // Trail-off handling
    // When user's voice fades gradually (thinking/hesitating)
    trailoffExtraMs: 1200,    // Slightly shorter wait (from 1400ms) - less awkward silence
    
    // Probability history for trail-off detection
    probHistoryLength: 18,    // Slightly shorter window (from 20) for more responsive detection
    
    // Trail-off detection thresholds
    // These define what counts as "gradual fadeout" vs "sudden stop"
    trailoffMinDecline: 0.18, // Slightly higher (from 0.15) - clearer trail-off signal needed
    trailoffMaxDecline: 0.68, // Slightly lower (from 0.70) - wider range for trail-off
};

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// DYNAMIC ADJUSTMENT HELPERS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

/**
 * Adjust VAD sensitivity based on conversation state
 * Makes the system feel more natural by adapting to context
 */
function getAdaptiveThresholds(conversationState) {
    const config = {...VAD_CONFIG};
    
    // If SARA just asked a question, be more sensitive to response
    if (conversationState.lastMessageWasQuestion) {
        config.positiveSpeechThreshold *= 0.95;  // 5% more sensitive
        config.redemptionMs *= 0.9;              // 10% faster response
    }
    
    // If conversation is emotional, allow longer pauses
    if (conversationState.isEmotional) {
        config.redemptionMs *= 1.15;             // 15% longer pauses
        config.trailoffExtraMs *= 1.1;           // 10% more patience
    }
    
    // If user tends to speak slowly, be more patient
    if (conversationState.userPace === 'slow') {
        config.redemptionMs *= 1.2;              // 20% longer pauses
        config.trailoffExtraMs *= 1.15;          // 15% more wait time
    } else if (conversationState.userPace === 'fast') {
        config.redemptionMs *= 0.85;             // 15% shorter pauses
        config.trailoffExtraMs *= 0.9;           // 10% less wait time
    }
    
    return config;
}

/**
 * Calculate optimal pause before SARA responds
 * Creates natural conversation rhythm
 */
function calculateResponseDelay(userMessage, conversationState) {
    let baseDelay = 200; // ms
    
    // Longer pause for complex questions
    const wordCount = userMessage.trim().split(/\s+/).length;
    if (wordCount > 15) {
        baseDelay += 150;
    }
    
    // Shorter pause for simple greetings
    const simpleGreetings = ['hi', 'hello', 'hey', 'yes', 'no', 'okay', 'thanks'];
    if (simpleGreetings.includes(userMessage.toLowerCase().trim())) {
        baseDelay = 100;
    }
    
    // Add slight randomness for naturalness
    baseDelay *= (0.9 + Math.random() * 0.2);
    
    return baseDelay;
}

/**
 * Detect if user's message is a question
 * Helps system anticipate expected response pattern
 */
function isQuestion(message) {
    const msg = message.trim().toLowerCase();
    
    // Question marks
    if (msg.includes('?')) return true;
    
    // Question words
    const questionWords = ['who', 'what', 'when', 'where', 'why', 'how', 'can', 'could', 'would', 'should', 'is', 'are', 'do', 'does'];
    const firstWord = msg.split(/\s+/)[0];
    if (questionWords.includes(firstWord)) return true;
    
    return false;
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// NATURAL PAUSE PATTERNS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

/**
 * Patterns that indicate user might continue speaking
 * System should wait a bit longer before responding
 */
const CONTINUATION_INDICATORS = [
    'and', 'but', 'so', 'also', 'then', 'because', 'since',
    'although', 'however', 'meanwhile', 'furthermore'
];

/**
 * Check if message ends with a continuation indicator
 */
function endsWithContinuation(message) {
    const words = message.trim().toLowerCase().split(/\s+/);
    const lastWord = words[words.length - 1].replace(/[.,!?]/g, '');
    return CONTINUATION_INDICATORS.includes(lastWord);
}

/**
 * Comfortable silence durations (ms) for different contexts
 * These feel natural, not awkward
 */
const COMFORTABLE_SILENCES = {
    afterGreeting: 300,
    afterQuestion: 400,
    afterStatement: 500,
    afterEmotional: 600,
    afterLongMessage: 700,
};
