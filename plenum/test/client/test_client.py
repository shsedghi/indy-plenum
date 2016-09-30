import pytest
from raet.raeting import AutoMode

from plenum.test.conftest import clientAndWallet1
from plenum.test.helper import *

nodeCount = 7

F = getMaxFailures(nodeCount)

whitelist = ['signer not configured so not signing',
             'for EmptySignature',
             'discarding message',
             'found legacy entry']  # warnings


def checkResponseRecvdFromNodes(client, expectedCount: int):
    respCount = 0
    for (resp, nodeNm) in client.inBox:
        if resp.get(OP_FIELD_NAME) in (REQACK, REPLY):
            respCount += 1
    assert respCount == expectedCount


# noinspection PyIncorrectDocstring
def testGeneratedRequestSequencing(tdir_for_func):
    """
    Request ids must be generated in an increasing order
    """
    with TestNodeSet(count=4, tmpdir=tdir_for_func) as nodeSet:
        w = Wallet('test')
        w.addSigner()

        operation = randomOperation()

        request = w.signOp(operation)
        assert request.reqId == 1

        request = w.signOp(operation)
        assert request.reqId == 2

        request = w.signOp(randomOperation())
        assert request.reqId == 3

        s2 = w.addSigner()

        request = w.signOp(randomOperation(), s2.identifier)
        assert request.reqId == 1

        request = w.signOp(randomOperation())
        assert request.reqId == 4


# noinspection PyIncorrectDocstring
def testClientShouldNotBeAbleToConnectToNodesNodeStack(pool):
    """
    Client should not be able to connect to nodes in the node's nodestack
    """

    async def go(ctx):
        for n in ctx.nodeset:
            n.nodestack.keep.auto = AutoMode.never

        nodestacksVersion = {k: v.ha for k, v in ctx.nodeset.nodeReg.items()}
        client1, _ = genTestClient(nodeReg=nodestacksVersion, tmpdir=ctx.tmpdir)
        ctx.looper.add(client1)
        with pytest.raises(NotConnectedToAny):
            await client1.ensureConnectedToNodes()

    pool.run(go)


# noinspection PyIncorrectDocstring
def testSendRequestWithoutSignatureFails(pool):
    """
    A client request sent without a signature fails with an EmptySignature
    exception
    """

    async def go(ctx):
        client1, wallet = genTestClient(ctx.nodeset, tmpdir=ctx.tmpdir)

        # remove the client's ability to sign
        assert wallet._getIdData().signer
        wallet._getIdData().signer = None
        assert not wallet._getIdData().signer

        ctx.looper.add(client1)
        await client1.ensureConnectedToNodes()

        request = Request(randomOperation())
        request = client1.submitReqs(request)[0]
        with pytest.raises(AssertionError):
            for node in ctx.nodeset:
                await eventually(
                        checkLastClientReqForNode, node, request,
                        retryWait=1, timeout=10)

        for n in ctx.nodeset:
            params = n.spylog.getLastParams(Node.reportSuspiciousClient)
            frm = params['clientName']
            reason = params['reason']

            assert frm == client1.name
            assert "SuspiciousClient" in reason
            assert "EmptySignature" in reason

            params = n.spylog.getLastParams(Node.discard)
            reason = params["reason"]
            (msg, frm) = params["msg"]
            assert msg == request.__dict__
            assert frm == client1.name
            assert "SuspiciousClient" in reason
            assert "EmptySignature" in reason

    pool.run(go)


CLI_REQ = pytest.mark.rbft_spec(section="IV", subsection="B", step=1)


@CLI_REQ("A client connects to all the nodes")
def testClientConnectsToAllNodes(client1):
    pass


@CLI_REQ("A client sends a request to all the nodes")
def testRequestFullRoundTrip(replied1):
    pass


# noinspection PyIncorrectDocstring
def testEveryNodeRepliesWithNoFaultyNodes(looper, client1, replied1):
    """
    Every node will send a reply to the client when there are no faulty nodes in
    the system
    """

    def chk():
        receivedReplies = getRepliesFromClientInbox(client1.inBox,
                                                    replied1.reqId)
        print(receivedReplies)
        assert len(receivedReplies) == nodeCount

    looper.run(eventually(chk))


# noinspection PyIncorrectDocstring
def testReplyWhenRepliesFromAllNodesAreSame(looper, client1, wallet1):
    """
    When there are not faulty nodes, the client must get a reply from all the
    nodes.
    """
    request = sendRandomRequest(wallet1, client1)
    looper.run(
            eventually(checkResponseRecvdFromNodes, client1,
                       2 * nodeCount * request.reqId,
                       retryWait=.25, timeout=15))
    checkResponseCorrectnessFromNodes(client1.inBox, request.reqId, F)


# noinspection PyIncorrectDocstring
def testReplyWhenRepliesFromExactlyFPlusOneNodesAreSame(looper,
                                                        client1,
                                                        wallet1):
    """
    When only :math:`2f+1` replies from the nodes are matching, the client
    would accept the reply
    """
    request = sendRandomRequest(wallet1, client1)
    # exactly f + 1 => (3) nodes have correct responses
    # modify some (numOfResponses of type REPLY - (f + 1)) => 4 responses to
    # have a different operations
    looper.run(
            eventually(checkResponseRecvdFromNodes, client1,
                       2 * nodeCount * request.reqId,
                       retryWait=.25, timeout=15))

    replies = (msg for msg, frm in client1.inBox
               if msg[OP_FIELD_NAME] == REPLY and
               msg[f.RESULT.nm][f.REQ_ID.nm] == request.reqId)

    # change two responses to something different
    for i in range(2):
        msg = next(replies)
        msg[f.RESULT.nm][TXN_ID] = str(i) + "Some random id"

    checkResponseCorrectnessFromNodes(client1.inBox, request.reqId, F)


# noinspection PyIncorrectDocstring
def testReplyWhenRequestAlreadyExecuted(looper, nodeSet, client1, sent1):
    """
    When a request has already been executed the previously executed reply
    will be sent again to the client. An acknowledgement will not be sent
    for a repeated request.
    """
    # Since view no is always zero in the current setup
    looper.run(eventually(checkSufficientRepliesRecvd,
                          client1.inBox,
                          sent1.reqId,
                          2,
                          retryWait=.5,
                          timeout=5))
    originalRequestResponsesLen = nodeCount * 2
    duplicateRequestRepliesLen = nodeCount  # for a duplicate request we need to
    client1.nodestack._enqueueIntoAllRemotes(sent1, None)

    def chk():
        assertLength([response for response in client1.inBox
                      if (response[0].get(f.RESULT.nm) and
                       response[0][f.RESULT.nm][f.REQ_ID.nm] == sent1.reqId) or
                      (response[0].get(OP_FIELD_NAME) == REQACK and
                       response[0].get(f.REQ_ID.nm) == sent1.reqId)],
                     originalRequestResponsesLen + duplicateRequestRepliesLen)

    looper.run(eventually(
            chk,
            retryWait=1,
            timeout=20))

# noinspection PyIncorrectDocstring
<<<<<<< Updated upstream
def testReplyMatchesRequest(looper, client1, wallet1):
    request = sendRandomRequest(wallet1, client1)
    req1Amount = request.operation['amount']
    looper.run(eventually(checkResponseRecvdFromNodes, client1, 2 * nodeCount, retryWait=.25, timeout=15))
    replies = [r[0]['result']['amount'] for r in client1.inBox if r[0]['op'] == "REPLY"]
    assert all(r == replies[0] for r in replies)
    assert replies[0] == req1Amount
=======
def testReplyMatchesRequest(looper, nodeSet, tdir, up):

    def makeClient():
        client, wallet = clientAndWallet1(looper, nodeSet, tdir, up)
        looper.add(client)
        looper.run(client.ensureConnectedToNodes())
        return client, wallet

    # creating clients
    numOfClients = 10
    clients = set()
    for i in range(numOfClients):
        caw = makeClient()
        clients.add(caw)

    experiments = 5
    for i in range(1, experiments + 1):

        # sending requests
        requests = {}

        for client, wallet in clients:
            request = sendRandomRequest(wallet, client)
            requests[client] = (request.reqId, request.operation['amount'])

        # checking results
        for client, wallet in clients:
            looper.run(eventually(checkResponseRecvdFromNodes,
                                  client,
                                  2 * nodeCount * i,
                                  retryWait=.25,
                                  timeout=15))

            (reqId, sentAmount) = requests[client]
            print("Expected amount for request {} is {}".format(reqId, sentAmount))

            replies = [r[0]['result']['amount']
                       for r in client.inBox
                       if r[0]['op'] == 'REPLY'
                       and r[0]['result']['reqId'] == reqId]

            assert all(replies[0] == r for r in replies)
            assert replies[0] == sentAmount
>>>>>>> Stashed changes
