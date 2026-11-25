"""Exact-anchor patch for receive ordering in the pinned edgetunnel module."""


def patch_sstp(text):
    def replace(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise RuntimeError('SSTP upstream anchor changed: ' + old[:80])
        text = text.replace(old, new)

    # Most TLS socket reads contain many complete SSTP frames. Avoid creating
    # four timeout timers/promises per already-buffered frame.
    replace('''const readPacket = async (timeoutMs = CONNECT_TIMEOUT_MS) => {
\t\tconst header = await withTimeout(readBytes(4), timeoutMs, 'SSTP read timeout');''', '''const readPacket = async (timeoutMs = CONNECT_TIMEOUT_MS) => {
        if (bufferedBytes.length >= 4) {
            const length = readSstpUint16(bufferedBytes, 2) & 0x0fff;
            if (length < 4) throw new Error('Invalid SSTP packet length');
            if (bufferedBytes.length >= length) {
                const packet = {isControl: (bufferedBytes[1] & 1) !== 0, body: bufferedBytes.subarray(4, length)};
                bufferedBytes = bufferedBytes.subarray(length);
                return packet;
            }
        }
\t\tconst header = await withTimeout(readBytes(4), timeoutMs, 'SSTP read timeout');''')
    replace('''\t\tconst buildTcpFrame = (flags, payload = SSTP_EMPTY_BYTES) => {''', '''        let ipIdentifier = randomSstpUint16();
\t\tconst buildTcpFrame = (flags, payload = SSTP_EMPTY_BYTES) => {''')
    replace('''\t\t\tconst tcpLength = 20 + payloadLength;''', '''            const tcpHeaderLength = (flags & 0x02) ? 24 : 20;
\t\t\tconst tcpLength = tcpHeaderLength + payloadLength;''')
    replace('view.setUint16(12, randomSstpUint16());', 'view.setUint16(12, ipIdentifier++ & 0xffff);')
    replace('frame[40] = 0x50;', 'frame[40] = (tcpHeaderLength / 4) << 4;')
    replace('if (payloadLength) frame.set(bytes, 48);', '''if (flags & 0x02) frame.set([2, 4, SSTP_TCP_MSS >> 8, SSTP_TCP_MSS & 0xff], 48);
            if (payloadLength) frame.set(bytes, 28 + tcpHeaderLength);''')

    start = text.index('\t\tconst matchIncomingIpPacket = ipPacket => {')
    end = text.index('\n\n\t\tawait withTimeout(writer.write(buildTcpFrame(0x02))', start)
    replace(text[start:end], '''\t\tconst matchIncomingIpPacket = ipPacket =>
            parseIpv4Tcp(ipPacket, sourcePort, targetPort, sourceAddress, destinationAddress);''')
    replace('if (!tcp || (tcp.flags & 0x12) !== 0x12) continue;',
            'if (!tcp || (tcp.flags & 0x12) !== 0x12 || tcp.acknowledgment !== sequenceNumber) continue;')
    replace("if (!tcpReady) throw new Error('TCP handshake through SSTP timed out');",
            "if (!tcpReady) throw new Error('TCP handshake through SSTP timed out');\n        const receiveQueue = new TcpReceiveQueue(acknowledgementNumber);")
    start = text.index('\t\t\t\t\tif (incoming.payloadOffset < ppp.ipPacket.byteLength) {')
    end = text.index('\n\t\t\t\t\tif (bufferedBytes.byteLength < 4 || pendingLength >= 32768) flush();', start)
    replace(text[start:end], '''                    if (incoming.flags & 0x04) throw new Error('SSTP remote TCP reset');
                    const payload = ppp.ipPacket.subarray(incoming.payloadOffset, incoming.payloadEnd);
                    const previousAck = acknowledgementNumber;
                    const received = receiveQueue.push(incoming.sequence, payload, !!(incoming.flags & 0x01));
                    acknowledgementNumber = received.next;
                    for (const chunk of received.chunks) {
                        pendingChunks.push(chunk);
                        pendingLength += chunk.length;
                    }
                    if (received.closed) {
                        flush();
                        await writer.write(buildTcpFrame(0x11));
                        try { streamController?.close() } catch {}
                        close();
                        return;
                    }
                    // Acknowledge gaps/duplicates promptly; never ACK past missing bytes.
                    if (payload.length && (incoming.sequence !== previousAck || !received.chunks.length))
                        await writer.write(buildTcpFrame(0x10));
''')
    replace('''\t\t\t} catch (error) {
\t\t\t\tconst controller = streamController;''', '''\t\t\t} catch (error) {
                console.warn('SSTP receive stopped', error.message);
\t\t\t\tconst controller = streamController;''')
    old = 'if (packet.isControl) continue;'
    if text.count(old) != 3:
        raise RuntimeError('SSTP control handling anchors changed')
    text = text.replace(old, '''if (packet.isControl) {
                const reply = sstpControlReply(packet.body);
                if (reply) await writer.write(reply);
                const controlType = readSstpUint16(packet.body);
                if (controlType === 5 || controlType === 6) throw new Error('SSTP peer disconnected');
                continue;
            }''')
    old = 'if (!ppp || ppp.protocol !== 0x0021) continue;'
    if text.count(old) != 2:
        raise RuntimeError('SSTP PPP handling anchors changed')
    text = text.replace(old, '''if (ppp?.protocol === 0xc021) {
                const reply = pppEchoReply(ppp.rawPacket);
                if (reply) await writer.write(buildSstpDataPacket(reply));
            }
            if (!ppp || ppp.protocol !== 0x0021) continue;''')
    return "import {TcpReceiveQueue, parseIpv4Tcp, sstpControlReply, pppEchoReply} from '../sstp-tcp.mjs';\n" + text
