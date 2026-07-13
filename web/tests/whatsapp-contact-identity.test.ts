import test from 'node:test';
import assert from 'node:assert/strict';

import {
    countContactNames,
    describeWhatsAppContact,
    groupWhatsAppContacts,
} from '../src/lib/whatsapp-contact-identity.js';

test('prefers a valid alternate phone over a WhatsApp LID', () => {
    const identity = describeWhatsAppContact('120363409354083235', {
        push_name: 'Lee',
        alt_id: '50376016721@s.whatsapp.net',
    });
    assert.equal(identity.phoneNumber, '+50376016721');
    assert.equal(identity.formattedNumber, '+503 7601 6721');
    assert.equal(identity.country, 'SV');
    assert.equal(identity.technicalId, '120363409354083235');
});

test('treats an ordinary international JID as the phone number', () => {
    const identity = describeWhatsAppContact('50582578411');
    assert.equal(identity.phoneNumber, '+50582578411');
    assert.equal(identity.formattedNumber, '+505 8257 8411');
    assert.equal(identity.country, 'NI');
    assert.equal(identity.technicalId, null);
});

test('groups records only when they resolve to the same phone number', () => {
    const groups = groupWhatsAppContacts(
        ['120363409354083235', '50376016721', '50582578411'],
        {
            '120363409354083235': { push_name: 'Lee', alt_id: '50376016721@s.whatsapp.net' },
            '50376016721': { push_name: 'Lee' },
            '50582578411': { push_name: 'Lee' },
        },
    );
    assert.equal(groups.length, 2);
    assert.deepEqual(groups[0].ids, ['120363409354083235', '50376016721']);
    assert.equal(countContactNames(groups).get('lee'), 2);
});
