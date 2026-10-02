// A synchronous FIFO: a memory, two pointers and an occupancy count.

module fifo #(
    parameter int DEPTH = 4,
    parameter int W     = 8
) (
    input  logic         clk,
    input  logic         rst,
    input  logic         push,
    input  logic         pop,
    input  logic [W-1:0] din,
    output logic [W-1:0] dout,
    output logic         empty,
    output logic         full
);
  logic [W-1:0] mem[DEPTH];
  logic [1:0] wptr, rptr;
  logic [2:0] count;

  always_ff @(posedge clk) begin
    if (rst) begin
      wptr  <= '0;
      rptr  <= '0;
      count <= '0;
    end else begin
      if (push && !full) begin
        mem[wptr] <= din;
        wptr <= wptr + 1'b1;
      end
      if (pop && !empty) rptr <= rptr + 1'b1;
      count <= count + 3'(push && !full) - 3'(pop && !empty);
    end
  end

  assign dout  = mem[rptr];
  assign empty = count == 3'd0;
  assign full  = count == 3'(DEPTH);
endmodule
