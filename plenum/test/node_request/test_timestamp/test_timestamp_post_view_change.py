from stp_core.loop.eventually import eventually

from plenum.test.helper import send_reqs_to_nodes_and_verify_all_replies, \
    sendRandomRequests, checkViewNoForNodes
from plenum.test.node_request.test_timestamp.helper import make_clock_faulty, \
    get_timestamp_suspicion_count
from plenum.test.test_node import ensureElectionsDone, getNonPrimaryReplicas
from plenum.test.view_change.helper import ensure_view_change

Max3PCBatchSize = 4

from plenum.test.batching_3pc.conftest import tconf

# lot of requests will be sent and multiple view changes are done
TestRunningTimeLimitSec = 200


def test_new_primary_has_wrong_clock(tconf, looper, txnPoolNodeSet, client1,
                                wallet1, client1Connected):
    """
    One of non-primary has a bad clock, it raises suspicions but orders
    requests after getting PREPAREs. Then a view change happens this
    non-primary with the bad clock becomes the new primary but is not able to
    get any of it's PRE-PREPAREs ordered. Eventually another view change
    happens and a new primary is elected the pool is functional again
    :return:
    """
    # The node having the bad clock, this node will be primary after view change
    faulty_node = getNonPrimaryReplicas(txnPoolNodeSet, 0)[0].node
    make_clock_faulty(faulty_node)

    assert not faulty_node.master_replica.isPrimary
    # faulty_node replies too
    send_reqs_to_nodes_and_verify_all_replies(looper, wallet1, client1,
                                              Max3PCBatchSize * 3)

    ledger_sizes = {node.name: node.domainLedger.size for node in txnPoolNodeSet}
    susp_counts = {node.name: get_timestamp_suspicion_count(node) for node in txnPoolNodeSet}
    ensure_view_change(looper, txnPoolNodeSet)
    ensureElectionsDone(looper=looper, nodes=txnPoolNodeSet)

    # After view change, faulty_node is primary
    assert faulty_node.master_replica.isPrimary

    old_view_no = txnPoolNodeSet[0].viewNo

    # Requests are sent
    for _ in range(5):
        sendRandomRequests(wallet1, client1, 2)
        looper.runFor(.2)

    def chk():
        for node in [n for n in txnPoolNodeSet if n != faulty_node]:
            # Each non faulty node raises suspicion
            assert get_timestamp_suspicion_count(node) > susp_counts[node.name]
            # Ledger does not change
            assert node.domainLedger.size == ledger_sizes[node.name]

        assert faulty_node.domainLedger.size == ledger_sizes[faulty_node.name]

    looper.run(eventually(chk, retryWait=1))

    # Eventually another view change happens
    looper.run(eventually(checkViewNoForNodes, txnPoolNodeSet, old_view_no + 1,
                          retryWait=1, timeout=2*tconf.PerfCheckFreq))
    ensureElectionsDone(looper=looper, nodes=txnPoolNodeSet)

    # After view change, faulty_node is no more the primary
    assert not faulty_node.master_replica.isPrimary

    # All nodes reply
    send_reqs_to_nodes_and_verify_all_replies(looper, wallet1, client1,
                                              Max3PCBatchSize * 2)
